"""
The deterministic checks. Each takes the fetched data and returns a CheckResult.

Plain functions over plain data: no model calls, no network. That keeps every
flag explainable ("this field, this value, this rule") and testable on real
Companies House responses.

Status meaning:
  CLEAR          looked, nothing found
  INFO           looked, found things worth reading that don't change the route
  FLAG           looked, found something that changes the route
  COULDNT_CHECK  didn't get an answer (and says why)
  NOT_APPLICABLE doesn't apply to this case
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from src.prescreen.identity import normalise_name
from src.prescreen.models import CheckResult, CheckStatus, Finding, PreScreenRequest, Route, SourceRef
from src.prescreen.rules import THRESHOLDS as T
from src.prescreen.sources.companies_house import PUBLIC_SITE, public_url
from src.prescreen.sources.gazette import NOTICE_NAMES
from src.prescreen.sources.http import NOT_FOUND, SourceError
from src.prescreen.sources.sanctions import PUBLIC_URL as SANCTIONS_URL
from src.prescreen.sources.sanctions import SanctionsList, Screened, screen

INSOLVENT_STATUSES = {
    "liquidation", "administration", "receivership", "insolvency-proceedings", "voluntary-arrangement",
}
STATUS_WORDS = {
    "liquidation": "in liquidation", "administration": "in administration", "receivership": "in receivership",
    "insolvency-proceedings": "in insolvency proceedings", "voluntary-arrangement": "in a voluntary arrangement",
}
CLOSED_STATUSES = {"dissolved", "removed", "closed", "converted-closed"}
UK_COUNTRIES = {"united kingdom", "england", "wales", "scotland", "northern ireland",
                "england and wales", "england & wales", "great britain", "uk"}
# Jurisdictions where ownership is hard to see. DRAFT list: a starting point for an underwriter to edit.
SECRECY_JURISDICTIONS = {
    "british virgin islands", "bvi", "cayman islands", "seychelles", "panama", "belize", "marshall islands",
    "anguilla", "bermuda", "bahamas", "vanuatu", "samoa", "nevis", "saint kitts and nevis", "labuan",
    "united arab emirates", "uae", "gibraltar", "isle of man", "jersey", "guernsey", "liechtenstein",
}
REGULATED_SIC_PREFIXES = ("64", "65", "66")
OFFICER_ROLES = {
    "director", "corporate-director", "nominee-director", "corporate-nominee-director",
    "llp-member", "llp-designated-member", "corporate-llp-member", "corporate-llp-designated-member",
    "member-of-an-administrative-organ", "member-of-a-management-organ", "member-of-a-supervisory-organ",
}
SMALL_ACCOUNT_TYPES = {"small", "total-exemption-small", "total-exemption-full", "unaudited-abridged",
                       "audited-abridged", "abridged"}
# Size limits in force for financial years from 6 April 2025 (turnover).
MICRO_TURNOVER = 1_000_000
SMALL_TURNOVER = 15_000_000


def status_words(status: str) -> str:
    return STATUS_WORDS.get(status, status.replace("-", " "))


@dataclass
class CompanyData:
    number: str
    profile: dict[str, Any]
    today: date
    officers: Any = None
    pscs: Any = None
    psc_statements: Any = None
    charges: Any = None
    insolvency: Any = None
    gazette_filings: Any = None                                  # filing history, category gazette
    gazette_notices: Any = None                                  # The Gazette insolvency notices
    appointments: dict[str, Any] = field(default_factory=dict)   # officer_id -> response
    linked_profiles: dict[str, Any] = field(default_factory=dict)  # company number -> profile
    linked_insolvency: dict[str, Any] = field(default_factory=dict)  # company number -> /insolvency response
    disqualified: dict[str, Any] = field(default_factory=dict)   # name -> search response
    disq_details: dict[str, Any] = field(default_factory=dict)   # self link -> detail
    parent_pscs: dict[str, Any] = field(default_factory=dict)    # parent number -> PSC response

    @property
    def name(self) -> str:
        return self.profile.get("company_name", "")


def _ch(number: str, suffix: str = "", label: str = "Companies House") -> SourceRef:
    return SourceRef(label=label, url=public_url(number, suffix))


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%d/%m/%Y").date()
        except ValueError:
            return None


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def _years_ago(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 Feb
        return today.replace(year=today.year - years, day=28)


def _couldnt(check_id: str, title: str, reason: str, critical: bool, sources=None) -> CheckResult:
    return CheckResult(check_id=check_id, title=title, status=CheckStatus.COULDNT_CHECK,
                       summary=f"Couldn't check: {reason}", not_checked_reason=reason,
                       critical=critical, sources=sources or [])


def _err_text(value: Any) -> str:
    return value.plain if isinstance(value, SourceError) else "no response"


def _join(parts: list[str]) -> str:
    return " ".join(p.strip() if p.strip().endswith((".", "?", "!")) else p.strip() + "." for p in parts if p.strip())


def _short_discipline(line: str) -> tuple[str, str]:
    """'Fines | 07/07/2025 | A final notice has been issued ... https://...pdf' -> ('fine, 7 Jul 2025: <gist>', url).
    The Register's text runs to a paragraph; the underwriter needs the kind, the date and the link."""
    url = (re.findall(r"https?://\S+", line) or [""])[0].rstrip(".,;)")
    parts = [p.strip() for p in re.sub(r"https?://\S+", "", line).split("|")]
    kind = (parts[0] if parts else "action").rstrip("s").lower() or "action"
    when = _parse_date(parts[1]) if len(parts) > 1 else None
    gist = parts[2] if len(parts) > 2 else ""
    m = re.search(r"because (?:it|they|the firm)?\s*(.+?)(?:\.|;| \(| during |$)", gist)
    gist = (m.group(1) if m else gist.split(".")[0]).strip()
    if len(gist) > 140:
        gist = gist[:137].rsplit(" ", 1)[0] + "…"
    head = f"{kind}, {when:%d %b %Y}" if when else kind
    return head + (f" ({gist})" if gist else ""), url


def _finish(check_id: str, title: str, findings: list[Finding], clear_summary: str,
            sources: list[SourceRef], critical: bool, info_summary: str | None = None) -> CheckResult:
    """info_summary: what to say when there are only notes, for checks whose clear summary
    ("no changes") would contradict them. Defaults to the clear summary."""
    flagged = [f for f in findings if f.route != Route.NOTE]
    if flagged:
        status, summary = CheckStatus.FLAG, _join([f.text for f in flagged])
    elif findings:
        status, summary = CheckStatus.INFO, clear_summary if info_summary is None else info_summary
        if not summary:
            # Never an empty line above the notes (Revolut's FCA check, live 19 Oct).
            summary = _join([f.text for f in findings])
    else:
        status, summary = CheckStatus.CLEAR, clear_summary
    return CheckResult(check_id=check_id, title=title, status=status, summary=summary,
                       findings=findings, sources=sources, critical=critical)


# ---------------------------------------------------------------------------
# Officers
# ---------------------------------------------------------------------------

def is_corporate(officer: dict[str, Any]) -> bool:
    return officer.get("officer_role", "").startswith("corporate")


def officers_in_scope(data: CompanyData, include_recent: bool = True) -> list[dict[str, Any]]:
    """Active directors/LLP members, plus (optionally) those who resigned in the last 12 months."""
    if not isinstance(data.officers, dict):
        return []
    cutoff = data.today - timedelta(days=365)
    out = []
    for o in data.officers.get("items") or []:
        if o.get("officer_role") not in OFFICER_ROLES:
            continue
        resigned = _parse_date(o.get("resigned_on"))
        if resigned is None or (include_recent and resigned >= cutoff):
            out.append(o)
    return out


def active_officers(data: CompanyData) -> list[dict[str, Any]]:
    return officers_in_scope(data, include_recent=False)


def officer_id(officer: dict[str, Any]) -> str | None:
    path = ((officer.get("links") or {}).get("officer") or {}).get("appointments", "")
    m = re.search(r"/officers/([^/]+)/appointments", path)
    return m.group(1) if m else None


def _who(o: dict[str, Any]) -> str:
    return o.get("name", "") + (" (resigned " + o["resigned_on"] + ")" if o.get("resigned_on") else "")


def officers_truncated(data: CompanyData) -> bool:
    if not isinstance(data.officers, dict):
        return False
    items = data.officers.get("items") or []
    return int(data.officers.get("total_results") or len(items)) > len(items)


def people_to_check(data: CompanyData) -> tuple[list[dict[str, Any]], int]:
    """Natural-person officers to run director checks on, capped. Returns (list, total before cap)."""
    people = [o for o in officers_in_scope(data) if not is_corporate(o)]
    return people[: T["max_directors_checked"]], len(people)


# ---------------------------------------------------------------------------
# 1. Status
# ---------------------------------------------------------------------------

def check_status(data: CompanyData) -> CheckResult:
    p, n = data.profile, data.number
    status = p.get("company_status", "")
    detail = p.get("company_status_detail", "")
    src = [_ch(n)]
    findings: list[Finding] = []

    if not status:
        return _couldnt("status", "Company status", "Companies House returned no status", True, src)
    if status in CLOSED_STATUSES:
        findings.append(Finding(rule_id="company_closed", route=Route.OUTSIDE_APPETITE,
                                text=f"Company is {status.replace('-', ' ')} on Companies House.", sources=src))
    elif status in INSOLVENT_STATUSES:
        findings.append(Finding(rule_id="company_insolvent", route=Route.OUTSIDE_APPETITE,
                                text=f"Company is {status_words(status)}.", sources=src))
    elif status != "active":
        findings.append(Finding(rule_id="status_unusual", route=Route.REFER,
                                text=f"Unusual company status: {status}.", sources=src))

    if detail == "active-proposal-to-strike-off":
        kind = strike_off_kind(data)
        fsrc = [_ch(n, "/filing-history")]
        if kind == "voluntary":
            findings.append(Finding(rule_id="strike_off_voluntary", route=Route.OUTSIDE_APPETITE,
                                    text="The directors have applied to strike the company off (voluntary First "
                                         "Gazette notice): it intends to close.", sources=fsrc))
        elif kind == "compulsory":
            findings.append(Finding(rule_id="strike_off_compulsory", route=Route.REFER,
                                    text="Companies House has proposed compulsory strike-off, usually for missing "
                                         "filings. It's often suspended once filings catch up. Ask the broker.",
                                    sources=fsrc))
        else:
            findings.append(Finding(rule_id="strike_off_unknown", route=Route.REFER,
                                    text="Active proposal to strike off; couldn't tell from the filing history "
                                         "whether it is voluntary or compulsory.", sources=fsrc))
    if p.get("registered_office_is_in_dispute"):
        findings.append(Finding(rule_id="office_disputed", route=Route.REFER,
                                text="Registered office address is in dispute.", sources=src))
    if p.get("undeliverable_registered_office_address"):
        findings.append(Finding(rule_id="office_undeliverable", route=Route.REFER,
                                text="Post can't be delivered to the registered office.", sources=src))
    return _finish("status", "Company status", findings,
                   "Active. No strike-off proposal; registered office not disputed.", src, critical=True)


def strike_off_kind(data: CompanyData) -> str:
    """'voluntary' (GAZ1(A)), 'compulsory' (GAZ1) or 'unknown', from the latest gazette filing."""
    fh = data.gazette_filings
    if not isinstance(fh, dict):
        return "unknown"
    for item in fh.get("items") or []:  # newest first
        t = (item.get("type") or "").upper()
        if t == "GAZ1(A)":
            return "voluntary"
        if t == "GAZ1":
            return "compulsory"
    return "unknown"


# ---------------------------------------------------------------------------
# 2. Age and names
# ---------------------------------------------------------------------------

def check_age_and_names(data: CompanyData) -> CheckResult:
    p, n = data.profile, data.number
    src = [_ch(n)]
    findings: list[Finding] = []
    created = _parse_date(p.get("date_of_creation"))
    if not created:
        return _couldnt("age", "Age and name history", "no incorporation date", False, src)
    months = _months_between(created, data.today)
    if months < T["new_company_months"]:
        findings.append(Finding(rule_id="company_new", route=Route.NOTE,
                                text=f"Incorporated {created:%d %b %Y}, {months} months ago: no trading history "
                                     "of its own. Ask about any predecessor business.",
                                detail={"months": months}, sources=src))
    elif months < T["young_company_months"]:
        findings.append(Finding(rule_id="company_young", route=Route.NOTE,
                                text=f"Incorporated {created:%d %b %Y} ({months} months ago).",
                                detail={"months": months}, sources=src))

    for prev in p.get("previous_company_names") or []:
        ceased = _parse_date(prev.get("ceased_on"))
        if ceased and _months_between(ceased, data.today) < T["rename_recent_months"]:
            findings.append(Finding(
                rule_id="renamed_recently", route=Route.NOTE,
                text=f"Renamed from {prev.get('name')} on {ceased:%d %b %Y}. News about the current name from "
                     "before that date is about a different company.",
                detail={"previous_name": prev.get("name"), "ceased_on": prev.get("ceased_on")}, sources=src))
    return _finish("age", "Age and name history", findings,
                   f"Incorporated {created:%d %b %Y} ({months // 12} years ago).", src, critical=False)


# ---------------------------------------------------------------------------
# 3. Filings and accounts
# ---------------------------------------------------------------------------

def check_filings(data: CompanyData, req: PreScreenRequest | None = None) -> CheckResult:
    p, n = data.profile, data.number
    src = [_ch(n, "/filing-history")]
    accounts = p.get("accounts") or {}
    nxt = accounts.get("next_accounts") or {}
    cs = p.get("confirmation_statement") or {}
    if not accounts and not cs:
        return _couldnt("filings", "Filings and accounts", "no accounts or confirmation statement data", True, src)

    findings: list[Finding] = []
    due = _parse_date(nxt.get("due_on") or accounts.get("next_due"))
    overdue = nxt.get("overdue", accounts.get("overdue"))
    if overdue is None and due is not None:
        overdue = due < data.today
    if overdue:
        days = (data.today - due).days if due else None
        when = f" by {days} days (due {due:%d %b %Y})" if days is not None else ""
        if days is not None and days > T["overdue_serious_days"]:
            findings.append(Finding(rule_id="accounts_overdue_serious", route=Route.REFER,
                                    text=f"Accounts are overdue{when}.", detail={"days_overdue": days}, sources=src))
        elif days is None or days > T["overdue_refer_days"]:
            findings.append(Finding(rule_id="accounts_overdue", route=Route.REFER,
                                    text=f"Accounts are overdue{when}.", detail={"days_overdue": days}, sources=src))
        else:
            findings.append(Finding(rule_id="accounts_late", route=Route.NOTE,
                                    text=f"Accounts are overdue{when}.", detail={"days_overdue": days}, sources=src))
    if cs.get("overdue"):
        cs_due = _parse_date(cs.get("next_due"))
        findings.append(Finding(rule_id="confirmation_overdue", route=Route.REFER,
                                text="Confirmation statement is overdue" +
                                     (f" (was due {cs_due:%d %b %Y})." if cs_due else "."), sources=src))

    last = accounts.get("last_accounts") or {}
    last_type = last.get("type", "")
    made_up = _parse_date(last.get("period_end_on") or last.get("made_up_to"))
    turnover = req.declared_turnover_gbp if req else None
    trades = bool(req and req.declared_trade)
    dormant = last_type == "dormant" or "99999" in (p.get("sic_codes") or [])
    if dormant:
        if trades or turnover:
            findings.append(Finding(rule_id="dormant_but_trading", route=Route.REFER,
                                    text="Filed as dormant (or SIC 99999), but the proposal describes a trading "
                                         "business. Ask why.", sources=src))
        else:
            findings.append(Finding(rule_id="dormant", route=Route.NOTE,
                                    text="Filed as dormant / SIC 99999: a non-trading company.", sources=src))
    if turnover is not None and last_type:
        if last_type == "micro-entity" and turnover > MICRO_TURNOVER:
            findings.append(Finding(rule_id="accounts_size_mismatch", route=Route.REFER,
                                    text=f"Declared turnover £{turnover:,} but the last accounts were micro-entity "
                                         f"(turnover limit £{MICRO_TURNOVER:,}). Ask which is right.", sources=src))
        elif last_type in SMALL_ACCOUNT_TYPES and turnover > SMALL_TURNOVER:
            findings.append(Finding(rule_id="accounts_size_mismatch", route=Route.REFER,
                                    text=f"Declared turnover £{turnover:,} but the last accounts were filed as small "
                                         f"(turnover limit £{SMALL_TURNOVER:,}). Ask which is right.", sources=src))

    context = []
    if last_type:
        context.append(f"Last accounts: {last_type.replace('-', ' ')}" +
                       (f", made up to {made_up:%d %b %Y}" if made_up else ""))
    if due and not overdue:
        context.append(f"next due {due:%d %b %Y}")
    clear = "Accounts and confirmation statement up to date. " + (", ".join(context) + "." if context else "")
    return _finish("filings", "Filings and accounts", findings, clear.strip(), src, critical=True)


# ---------------------------------------------------------------------------
# 4. Insolvency (Companies House record + The Gazette)
# ---------------------------------------------------------------------------

def check_insolvency(data: CompanyData) -> CheckResult:
    n = data.number
    title = "Insolvency (Companies House and Gazette)"
    src = [_ch(n, "/insolvency"),
           SourceRef(label="The Gazette", url=f"https://www.thegazette.co.uk/insolvency/notice?text={n}")]
    findings: list[Finding] = []
    problems: list[str] = []

    links = data.profile.get("links") or {}
    has_link = "insolvency" in links or bool(data.profile.get("has_insolvency_history"))
    if has_link:
        ins = data.insolvency
        if isinstance(ins, SourceError) or ins is None or ins is NOT_FOUND:
            problems.append(f"the Companies House insolvency record couldn't be read ({_err_text(ins)})")
        else:
            for case in ins.get("cases") or [{}]:
                kind = (case.get("type") or "insolvency case").replace("-", " ")
                dates = ", ".join(f"{d.get('type', '').replace('-', ' ')} {d.get('date', '')}"
                                  for d in case.get("dates") or [])
                findings.append(Finding(rule_id="insolvency_history", route=Route.REFER,
                                        text=f"Insolvency case on record: {kind}" + (f" ({dates})" if dates else ""),
                                        detail=case, sources=src[:1]))

    notices = data.gazette_notices
    if isinstance(notices, SourceError) or notices is None:
        problems.append(f"The Gazette couldn't be searched ({_err_text(notices)})")
    else:
        recent_cut = data.today - timedelta(days=T["gazette_recent_days"])
        history_cut = _years_ago(data.today, T["director_lookback_years"])
        for nt in notices:
            published = _parse_date(nt.get("published"))
            if published is None or published < history_cut:
                continue
            # The feed's title is usually just the company name (BrewDog, live), so never use it as the notice name.
            code = nt.get("code", "")
            name = NOTICE_NAMES.get(code) or (f"Insolvency notice (code {code})" if code else "Insolvency notice")
            nsrc = [SourceRef(label=f"Gazette notice {nt.get('code', '')}", url=nt.get("url", ""))]
            if nt.get("code") == "2450" and published >= recent_cut:
                findings.append(Finding(rule_id="winding_up_petition", route=Route.OUTSIDE_APPETITE,
                                        text=f"Winding-up petition advertised in the Gazette on {published:%d %b %Y}.",
                                        sources=nsrc))
            else:
                findings.append(Finding(rule_id="gazette_insolvency_notice", route=Route.REFER,
                                        text=f"Gazette: {name}, {published:%d %b %Y}.", sources=nsrc))
    if problems and not findings:
        return _couldnt("insolvency", title, "; ".join(problems), True, src)
    result = _finish("insolvency", title, findings,
                     f"No insolvency record on Companies House and no Gazette insolvency notices in the last "
                     f"{T['director_lookback_years']} years.", src, critical=True)
    if problems:
        result.status = CheckStatus.COULDNT_CHECK
        result.not_checked_reason = "; ".join(problems)
    return result


# ---------------------------------------------------------------------------
# 5. Charges
# ---------------------------------------------------------------------------

def check_charges(data: CompanyData) -> CheckResult:
    n = data.number
    src = [_ch(n, "/charges")]
    links = data.profile.get("links") or {}
    if "charges" not in links and not data.profile.get("has_charges"):
        return CheckResult(check_id="charges", title="Charges", status=CheckStatus.CLEAR,
                           summary="No charges registered.", sources=src)
    ch = data.charges
    if isinstance(ch, SourceError) or ch is None or ch is NOT_FOUND:
        return _couldnt("charges", "Charges", f"charges couldn't be read ({_err_text(ch)})", False, src)
    items = ch.get("items") or []
    if int(ch.get("total_count") or len(items)) > len(items):
        return _couldnt("charges", "Charges", f"only {len(items)} of {ch.get('total_count')} charges were read",
                        False, src)
    outstanding = [c for c in items if c.get("status") in ("outstanding", "part-satisfied")]

    def lenders(c: dict) -> list[str]:
        return [x.get("name", "") for x in c.get("persons_entitled") or [] if x.get("name")]

    recent = [c for c in items
              if (d := _parse_date(c.get("created_on"))) and _months_between(d, data.today) < T["charge_burst_months"]]
    recent_lenders = sorted({l for c in recent for l in lenders(c)})
    findings = []
    if len(recent_lenders) >= T["charge_burst_lenders"]:
        findings.append(Finding(rule_id="charges_burst", route=Route.REFER,
                                text=f"Charges to {len(recent_lenders)} different lenders in the last "
                                     f"{T['charge_burst_months']} months ({', '.join(recent_lenders[:4])}). "
                                     "Can mean lender pressure.", sources=src))
    if outstanding:
        all_lenders = sorted({l for c in outstanding for l in lenders(c)})
        findings.append(Finding(rule_id="charges_outstanding", route=Route.NOTE,
                                text=f"{len(outstanding)} outstanding charge(s)" +
                                     (f" to {', '.join(all_lenders[:4])}" if all_lenders else "") +
                                     ". Check the loss payee on property cover.",
                                detail={"lenders": all_lenders}, sources=src))
    return _finish("charges", "Charges", findings,
                   f"{len(outstanding)} outstanding charge(s)." if outstanding else "All registered charges are satisfied.",
                   src, critical=False)


# ---------------------------------------------------------------------------
# 6. Directors: disqualification
# ---------------------------------------------------------------------------

def _name_tokens(name: str) -> frozenset[str]:
    s = re.sub(r"[^A-Za-z ]+", " ", (name or "").upper())
    return frozenset(t for t in s.split() if len(t) > 1 and t not in {"MR", "MRS", "MS", "DR", "SIR"})


def names_match(a: frozenset[str], b: frozenset[str]) -> bool:
    """Same person-name on two registers: equal, or one is the other plus/minus middle names."""
    if not a or not b:
        return False
    return a == b or (len(a & b) >= 2 and (a <= b or b <= a))


def _dob_from(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, dict):
        return value.get("year"), value.get("month")
    d = _parse_date(value) if isinstance(value, str) else None
    return (d.year, d.month) if d else (None, None)


def check_directors_disqualified(data: CompanyData) -> CheckResult:
    title = "Directors: disqualification"
    n = data.number
    if not isinstance(data.officers, dict):
        return _couldnt("directors_disqualified", title,
                        f"the officer list couldn't be read ({_err_text(data.officers)})", True, [_ch(n, "/officers")])
    people, total = people_to_check(data)
    if not officers_in_scope(data, include_recent=False):
        return CheckResult(check_id="directors_disqualified", title=title, status=CheckStatus.FLAG,
                           summary="No active directors or members on the register.", critical=True,
                           findings=[Finding(rule_id="no_directors", route=Route.REFER,
                                             text="No active directors or members on the register.",
                                             sources=[_ch(n, "/officers")])])
    src = [SourceRef(label="Companies House disqualified directors register",
                     url=f"{PUBLIC_SITE}/search/disqualified-officers")]
    findings, failed = [], []
    for d in people:
        name = d.get("name", "")
        res = data.disqualified.get(name)
        if isinstance(res, SourceError) or res is None:
            failed.append(name)
            continue
        hits = [] if res is NOT_FOUND else res.get("items") or []
        if res is not NOT_FOUND and int(res.get("total_results") or len(hits)) > len(hits):
            failed.append(name)  # common name: more hits than we read, so we can't say "not disqualified"
        y, m = _dob_from(d.get("date_of_birth"))
        mine = _name_tokens(name)
        for h in hits:
            if not names_match(_name_tokens(h.get("title", "")), mine):
                continue
            hy, hm = _dob_from(h.get("date_of_birth"))
            link = (h.get("links") or {}).get("self", "")
            hit_src = [SourceRef(label="Disqualification record", url=f"{PUBLIC_SITE}{link}" if link else src[0].url)]
            if y and hy and (y != hy or (m and hm and m != hm)):
                findings.append(Finding(rule_id="disq_name_only", route=Route.NOTE,
                                        text=f"{_who(d)}: a disqualified person has the same name but a different "
                                             f"date of birth ({hm}/{hy} vs {m}/{y}). Likely a different person.",
                                        sources=hit_src))
                continue
            if not (y and hy):
                findings.append(Finding(rule_id="disq_possible", route=Route.REFER,
                                        text=f"{_who(d)}: same name as a disqualified person, and no date of birth to "
                                             "compare. Check the record.", sources=hit_src))
                continue
            detail = data.disq_details.get(link)
            until = None
            if isinstance(detail, dict):
                periods = [x.get("disqualified_until") for x in detail.get("disqualifications") or []]
                until = max((p for p in periods if p), default=None)
            current = until is None or (_parse_date(until) or date.max) >= data.today
            findings.append(Finding(
                rule_id="director_disqualified" if current else "director_previously_disqualified",
                route=Route.OUTSIDE_APPETITE if current else Route.REFER,
                text=f"{_who(d)} matches a disqualified director on name and date of birth" +
                     (f"; disqualified until {until}." if until else "."),
                sources=hit_src))
    member = "member" if data.profile.get("type") == "llp" else "director"
    scope = (f"{len(people)} {member}(s)" + (f" of {total}" if total > len(people) else "") +
             " checked by name and date of birth, including any who resigned in the last 12 months")
    if total > len(people):
        findings.append(Finding(rule_id="directors_capped", route=Route.NOTE,
                                text=f"Only the first {len(people)} of {total} {member}s were checked."))
    if officers_truncated(data):
        failed.append("officers beyond the first page")
    if total > len(people):
        failed.append(f"{total - len(people)} {member}s over the {len(people)}-{member} limit")
    if failed:
        result = _couldnt("directors_disqualified", title,
                          f"not every {member} could be checked (" + "; ".join(failed[:4]) + ")", True, src)
        result.findings = findings
        return result
    return _finish("directors_disqualified", title, findings, scope + "; none disqualified.", src, critical=True)


# ---------------------------------------------------------------------------
# 7. Directors: other companies (insolvency links, phoenix pattern)
# ---------------------------------------------------------------------------

def _similar_names(a: str, b: str) -> bool:
    stop = {"LTD", "PLC", "LLP", "CO", "AND", "GROUP", "HOLDINGS", "SERVICES", "UK", "THE"}

    def stem(t: str) -> str:
        return t[:-1] if len(t) > 3 and t.endswith("S") else t

    ta = {stem(t) for t in normalise_name(a).split()} - stop
    tb = {stem(t) for t in normalise_name(b).split()} - stop
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.5


def linked_companies_to_profile(data: CompanyData) -> list[str]:
    """Dissolved companies (within the lookback) whose profile we need to see if they went through insolvency."""
    numbers: list[str] = []
    people, _ = people_to_check(data)
    history_cut = _years_ago(data.today, T["director_lookback_years"])
    for d in people:
        res = data.appointments.get(officer_id(d) or "")
        if not isinstance(res, dict):
            continue
        per_person = 0
        for a in res.get("items") or []:
            to = a.get("appointed_to") or {}
            num = to.get("company_number", "")
            if not num or num == data.number or to.get("company_status") != "dissolved":
                continue
            resigned = _parse_date(a.get("resigned_on"))
            if resigned and resigned < history_cut - timedelta(days=365 * 3):
                continue
            if num not in numbers and per_person < T["max_dissolved_profiles_per_director"]:
                numbers.append(num)
                per_person += 1
    return numbers[: T["max_dissolved_profiles_total"]]


def _went_insolvent(profile: Any) -> bool:
    if not isinstance(profile, dict):
        return False
    return "insolvency" in (profile.get("links") or {}) or bool(profile.get("has_insolvency_history"))


# When an insolvency began, from the Companies House insolvency record. The earliest of these dates on any case.
INSOLVENCY_START_DATES = {"petitioned-on", "ordered-to-wind-up-on", "wound-up-on", "administration-started-on",
                          "voluntary-arrangement-started-on", "instrumented-on", "moratorium-started-on"}
SOLVENT_CASES = {"members-voluntary-liquidation"}  # a solvent wind-up: the company paid its debts in full


def insolvency_start(record: Any) -> tuple[date | None, bool]:
    """(start date, solvent). solvent=True when every case on record is a members' voluntary liquidation.
    (None, False) when the record is missing or has no start date: timing unknown."""
    if not isinstance(record, dict):
        return None, False
    cases = record.get("cases") or []
    if cases and all((c.get("type") or "") in SOLVENT_CASES for c in cases):
        return None, True
    starts = [_parse_date(d.get("date")) for c in cases if (c.get("type") or "") not in SOLVENT_CASES
              for d in c.get("dates") or [] if d.get("type") in INSOLVENCY_START_DATES]
    starts = [x for x in starts if x]
    return (min(starts) if starts else None), False


def insolvency_summary(record: Any) -> dict[str, Any]:
    """The insolvency record in the shape the MCP tool returns, with the start date the director-link
    rule (19e) uses, so an agent can check the rule's timing against the same evidence."""
    if not isinstance(record, dict):
        return {"cases": [], "began_on": None, "solvent_wind_up": False,
                "note": "Companies House returned no insolvency record."}
    began, solvent = insolvency_start(record)
    cases = [{"type": c.get("type"), "number": c.get("number"),
              "dates": [{"type": d.get("type"), "date": d.get("date")} for d in c.get("dates") or []]}
             for c in record.get("cases") or []]
    return {"cases": cases, "began_on": began.isoformat() if began else None, "solvent_wind_up": solvent,
            "note": ("A members' voluntary liquidation: a solvent wind-up, debts paid in full." if solvent else
                     "began_on is the earliest petition, winding-up, administration, CVA or receivership date."
                     if began else "No start date on record: timing unknown.")}


def _relevant_window_cut(data: CompanyData) -> date:
    """A director who left before this can't be within the relevant window of an insolvency in the lookback."""
    return _years_ago(data.today, T["director_lookback_years"]) - timedelta(days=round(30.44 * T["director_relevant_months"]))


def linked_companies_to_profile(data: CompanyData) -> list[str]:
    """Dissolved companies (within the lookback) whose profile we need to see if they went through insolvency."""
    numbers: list[str] = []
    people, _ = people_to_check(data)
    cut = _relevant_window_cut(data)
    for d in people:
        res = data.appointments.get(officer_id(d) or "")
        if not isinstance(res, dict):
            continue
        per_person = 0
        for a in res.get("items") or []:
            to = a.get("appointed_to") or {}
            num = to.get("company_number", "")
            if not num or num == data.number or to.get("company_status") != "dissolved":
                continue
            resigned = _parse_date(a.get("resigned_on"))
            if resigned and resigned < cut:
                continue
            if num not in numbers and per_person < T["max_dissolved_profiles_per_director"]:
                numbers.append(num)
                per_person += 1
    return numbers[: T["max_dissolved_profiles_total"]]


def linked_insolvencies_to_read(data: CompanyData) -> list[str]:
    """Linked companies whose insolvency record we read for its start date: those in an insolvency status now,
    and dissolved ones that went through insolvency. Needs linked_profiles to be filled first."""
    numbers: list[str] = []
    people, _ = people_to_check(data)
    cut = _relevant_window_cut(data)
    for d in people:
        res = data.appointments.get(officer_id(d) or "")
        if not isinstance(res, dict):
            continue
        for a in res.get("items") or []:
            to = a.get("appointed_to") or {}
            num, st = to.get("company_number", ""), to.get("company_status", "")
            resigned = _parse_date(a.get("resigned_on"))
            if not num or num == data.number or num in numbers or (resigned and resigned < cut):
                continue
            if st in INSOLVENT_STATUSES or (st == "dissolved" and _went_insolvent(data.linked_profiles.get(num))):
                numbers.append(num)
    return numbers[: T["max_linked_insolvency_lookups"]]


def _months(a: date, b: date) -> int:
    """Whole months from a to b (negative if b is before a)."""
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def _gap_words(months: int) -> str:
    years, rest = divmod(months, 12)
    if not years:
        return f"{rest} month{'s' if rest != 1 else ''}"
    return f"{years} year{'s' if years != 1 else ''}" + (f" {rest} month{'s' if rest != 1 else ''}" if rest else "")


def classify_link(appointment: dict[str, Any], start: date | None, ended: date | None) -> tuple[str, str]:
    """How close a director was to a linked company's insolvency. Returns (tier, words):
      in_post   in post when it began, or left within director_in_post_months before (s.216 uses 12 months)
      near      left within director_relevant_months before (the insolvency conduct report covers 3 years)
      long_before  left earlier than that: not counted
      after     joined after it began: not counted
      unknown   no start date on record: counted, but at most refer
    `ended` (dissolution date) is used only when there's no start date; it's later than the start, so a
    director who left within the window of the dissolution is counted rather than excused."""
    resigned = _parse_date(appointment.get("resigned_on"))
    appointed = _parse_date(appointment.get("appointed_on"))
    when = start or ended
    if when is None:
        return "unknown", "timing unknown"
    approx = "" if start else " (dated by dissolution; start date not on record)"
    if appointed and appointed > when:
        return "after", f"joined {appointed:%b %Y}, after it {'began' if start else 'ended'}"
    if resigned is None or resigned >= when:
        rescue = appointed and _months(appointed, when) < T["rescue_join_months"]
        return "in_post", ("in post when it began" if start else "in post when it was dissolved") + approx + \
            (f"; joined only {_gap_words(max(_months(appointed, when), 0))} before, possibly a rescue appointment"
             if rescue else "")
    gap = _months(resigned, when)
    if gap < T["director_in_post_months"]:
        return "in_post", f"left {_gap_words(gap)} before it began" + approx
    if gap < T["director_relevant_months"]:
        return "near", f"left {_gap_words(gap)} before it began" + approx
    return "long_before", f"left {_gap_words(gap)} before it began" + approx


def check_directors_insolvency(data: CompanyData) -> CheckResult:
    title = "Directors: other companies"
    n = data.number
    if not isinstance(data.officers, dict):
        return _couldnt("directors_insolvency", title,
                        f"the officer list couldn't be read ({_err_text(data.officers)})", True, [_ch(n, "/officers")])
    people, total = people_to_check(data)
    created = _parse_date(data.profile.get("date_of_creation"))
    young = created is not None and _months_between(created, data.today) < T["phoenix_young_months"]
    history_cut = _years_ago(data.today, T["director_lookback_years"])
    findings, failed, notes, excused, unknown_any = [], [], [], [], []
    src = []
    unread_linked = 0
    member = "member" if data.profile.get("type") == "llp" else "director"

    for d in people:
        name, oid = _who(d), officer_id(d)
        if not oid:
            failed.append(name)
            continue
        res = data.appointments.get(oid)
        if isinstance(res, SourceError) or res is None or res is NOT_FOUND:
            failed.append(name)
            continue
        ref = SourceRef(label=f"{d.get('name', '')}: appointments", url=f"{PUBLIC_SITE}/officers/{oid}/appointments")
        src.append(ref)
        items = res.get("items") or []
        many = int(res.get("total_results") or len(items)) >= T["many_appointments"]
        insolvent: list[dict] = []
        dissolved_plain = 0
        for a in items:
            to = a.get("appointed_to") or {}
            num = to.get("company_number", "")
            if num == n:
                continue
            st = to.get("company_status", "")
            ended = None
            if st == "dissolved":
                prof = data.linked_profiles.get(num)
                if prof is None:
                    dissolved_plain += 1
                    continue
                if isinstance(prof, SourceError):
                    unread_linked += 1
                    dissolved_plain += 1
                    continue
                if not _went_insolvent(prof):
                    dissolved_plain += 1
                    continue
                ended = _parse_date(prof.get("date_of_cessation"))
            elif st not in INSOLVENT_STATUSES:
                continue
            record = data.linked_insolvency.get(num)
            start, solvent = insolvency_start(record)
            if solvent:
                excused.append(f"{_surname_of(d)}: {to.get('company_name')} ({num}) was a members' voluntary "
                               "liquidation, a solvent wind-up")
                continue
            when = start or ended
            if when is None:
                resigned = _parse_date(a.get("resigned_on"))
                if resigned is not None and resigned < history_cut:
                    continue  # as before: unknown timing, only directors in post within the lookback
            tier, words = classify_link(a, start, ended)
            if when is not None and when < history_cut:
                # Outside the lookback. Said out loud when it would otherwise have counted (Tesco, live: the
                # only director link began Apr 2021, so the route changed; that mustn't happen silently).
                if tier in ("in_post", "near") and when >= _relevant_window_cut(data):  # near misses only
                    excused.append(f"{_surname_of(d)}: {to.get('company_name')} ({num}), "
                                   f"{'began' if start else 'dissolved'} {when:%b %Y}, more than "
                                   f"{T['director_lookback_years']} years ago")
                continue
            if tier in ("long_before", "after"):
                excused.append(f"{_surname_of(d)}: {to.get('company_name')} ({num}), {words}")
                continue
            if tier == "unknown":
                words += " (insolvency record couldn't be read)" if isinstance(record, SourceError) else \
                    " (no start date on the insolvency record)"
                unknown_any.append(f"{to.get('company_name')} ({num})")
            insolvent.append({**to, "_tier": tier, "_words": words, "_status": status_words(st) if st != "dissolved"
                              else "insolvent, dissolved" + (f" {ended:%b %Y}" if ended else "")})
        if dissolved_plain:
            notes.append(f"{d.get('name', '')}: {dissolved_plain}")
        if res.get("truncated"):
            findings.append(Finding(rule_id="appointments_truncated", route=Route.NOTE,
                                    text=f"{name} has {res.get('total_results')} appointments; only the first "
                                         f"{len(items)} were read.", sources=[ref]))
        if not insolvent:
            continue

        def label(c: dict) -> str:
            return f"{c.get('company_name')} ({c.get('company_number')}, {c['_status']}; {c['_words']})"

        in_post = [c for c in insolvent if c["_tier"] == "in_post"]
        predecessor = [c for c in insolvent if _similar_names(c.get("company_name", ""), data.name)
                       and c["_tier"] in ("in_post", "unknown")]
        if predecessor and young:
            sure = predecessor[0]["_tier"] == "in_post"
            findings.append(Finding(
                rule_id="phoenix_pattern", route=Route.OUTSIDE_APPETITE if sure else Route.REFER,
                text=f"{name} was a {member} of {label(predecessor[0])}, which has a similar name, and this company "
                     f"is under {T['phoenix_young_months']} months old. Possible phoenix: s.216 prohibited-name "
                     "rules may apply. Ask the broker about the old company; a senior underwriter can override "
                     "for a genuine pre-pack sale." + ("" if sure else " Timing unknown, so capped at refer."),
                sources=[ref]))
        count = len(insolvent)
        # Only links where they were in post (or left within 12 months) can make a case outside appetite.
        route = Route.OUTSIDE_APPETITE if len(in_post) >= T["director_insolvent_decline"] else Route.REFER
        capped = many and route == Route.OUTSIDE_APPETITE
        if capped:
            route = Route.REFER
        findings.append(Finding(
            rule_id="director_insolvent_links", route=route,
            text=f"{name} was a {member} of {count} compan{'y' if count == 1 else 'ies'} that became insolvent "
                 f"in the last {T['director_lookback_years']} years: "
                 + ", ".join(label(c) for c in insolvent[:4]) +
                 (f" (and {count - 4} more)" if count > 4 else "") +
                 (f". They hold {res.get('total_results')} appointments (likely a group or non-executive), so "
                  "this is capped at refer" if capped else ""),
            detail={"count": count, "name": name, "in_post": len(in_post),
                    "near": sum(c["_tier"] == "near" for c in insolvent),
                    "unknown": sum(c["_tier"] == "unknown" for c in insolvent)}, sources=[ref]))

    if unknown_any:
        findings.append(Finding(
            rule_id="director_link_dates_unknown", route=Route.NOTE,
            text="When these insolvencies began isn't on record, so they're counted but can't make the case "
                 "outside appetite on their own: " + ", ".join(unknown_any[:6]) + "."))
    if excused:
        findings.append(Finding(
            rule_id="director_link_not_counted", route=Route.NOTE,
            text=f"Not counted ({member}s left long before the insolvency or joined after it, the wind-up was "
                 f"solvent, or it began more than {T['director_lookback_years']} years ago): " + "; ".join(excused[:8]) + (f"; and {len(excused) - 8} more" if len(excused) > 8 else "")
                 + "."))
    if notes:
        findings.append(Finding(
            rule_id="director_dissolved_context", route=Route.NOTE,
            text="Other companies dissolved without an insolvency record (not counted; closing dormant companies "
                 "is normal): " + "; ".join(notes)))
    if total > len(people):
        findings.append(Finding(rule_id="directors_capped", route=Route.NOTE,
                                text=f"Only the first {len(people)} of {total} {member}s were checked."))
    if officers_truncated(data):
        failed.append("officers beyond the first page")
    if total > len(people):
        failed.append(f"{total - len(people)} over the limit")
    if failed or unread_linked:
        why = []
        if failed:
            why.append(f"not every {member}'s other companies could be read (" + "; ".join(failed[:4]) + ")")
        if unread_linked:
            why.append(f"{unread_linked} dissolved companies couldn't be checked for insolvency")
        result = _couldnt("directors_insolvency", title, "; ".join(why), True, src)
        result.findings = findings
        return result
    return _finish("directors_insolvency", title, findings,
                   f"{len(people)} {member}(s), including any who resigned in the last 12 months: no links to "
                   f"companies that became insolvent in the last {T['director_lookback_years']} years while they "
                   "were in post or shortly before.",
                   src, critical=True)


def _surname_of(d: dict[str, Any]) -> str:
    name = d.get("name", "")
    return (name.split(",")[0] if "," in name else name.split(" ")[-1]).strip().title() or "a director"


# ---------------------------------------------------------------------------
# 8. Board and ownership changes (renewal view)
# ---------------------------------------------------------------------------

def check_changes(data: CompanyData) -> CheckResult:
    title = "Board and ownership changes (12 months)"
    n = data.number
    src = [_ch(n, "/officers"), _ch(n, "/persons-with-significant-control")]
    if not isinstance(data.officers, dict):
        return _couldnt("changes", title, f"the officer list couldn't be read ({_err_text(data.officers)})",
                        False, src)
    cutoff = data.today - timedelta(days=365)
    joined, left = [], []
    for o in data.officers.get("items") or []:
        if o.get("officer_role") not in OFFICER_ROLES:
            continue
        a, r = _parse_date(o.get("appointed_on")), _parse_date(o.get("resigned_on"))
        if a and a >= cutoff:
            joined.append(f"{o.get('name')} ({a:%b %Y})")
        if r and r >= cutoff:
            left.append(f"{o.get('name')} ({r:%b %Y})")
    psc_changes = []
    if isinstance(data.pscs, dict):
        for p in data.pscs.get("items") or []:
            notified, ceased = _parse_date(p.get("notified_on")), _parse_date(p.get("ceased_on"))
            if notified and notified >= cutoff:
                psc_changes.append(f"{p.get('name')} became a PSC ({notified:%b %Y})")
            if ceased and ceased >= cutoff:
                psc_changes.append(f"{p.get('name')} stopped being a PSC ({ceased:%b %Y})")
    findings = []
    if joined:
        findings.append(Finding(rule_id="officers_joined", route=Route.NOTE,
                                text="Appointed in the last 12 months: " + ", ".join(joined[:5]), sources=src[:1]))
    if left:
        findings.append(Finding(rule_id="officers_left", route=Route.NOTE,
                                text="Resigned in the last 12 months: " + ", ".join(left[:5]) +
                                     ". At renewal, check this was presented.", sources=src[:1]))
    if psc_changes:
        findings.append(Finding(rule_id="psc_changed", route=Route.NOTE,
                                text="Ownership changes: " + "; ".join(psc_changes[:4]) +
                                     ". At renewal, check this was presented.", sources=src[1:]))
    return _finish("changes", title, findings, "No director or ownership changes in the last 12 months.",
                   src, critical=False, info_summary=_join([f.text for f in findings]))


# ---------------------------------------------------------------------------
# 9. Ownership (PSC)
# ---------------------------------------------------------------------------

def active_pscs(data: CompanyData) -> list[dict[str, Any]]:
    if not isinstance(data.pscs, dict):
        return []
    return [p for p in data.pscs.get("items") or [] if not p.get("ceased_on") and not p.get("ceased")]


def uk_parent_number(psc: dict[str, Any]) -> str | None:
    ident = psc.get("identification") or {}
    country = (ident.get("country_registered") or ident.get("place_registered") or "").strip().lower()
    reg = re.sub(r"\s+", "", str(ident.get("registration_number") or "")).upper()
    if not reg or (country and country not in UK_COUNTRIES and "companies house" not in country):
        return None
    if re.fullmatch(r"\d{6,8}", reg):
        return reg.zfill(8)
    return reg if re.fullmatch(r"[A-Z]{2}\d{6}", reg) else None


def check_ownership(data: CompanyData) -> CheckResult:
    title = "Ownership (PSC register)"
    n = data.number
    src = [_ch(n, "/persons-with-significant-control")]
    if isinstance(data.pscs, SourceError) or data.pscs is None:
        return _couldnt("ownership", title, f"the PSC register couldn't be read ({_err_text(data.pscs)})", False, src)
    pscs = active_pscs(data)
    if isinstance(data.pscs, dict) and int(data.pscs.get("total_results") or 0) > len(data.pscs.get("items") or []):
        return _couldnt("ownership", title, "the PSC register has more entries than were read", False, src)
    statements = []
    if isinstance(data.psc_statements, dict):
        statements = [s for s in data.psc_statements.get("items") or [] if not s.get("ceased_on")]
    findings = []
    exempt = "exemptions" in (data.profile.get("links") or {})
    if not pscs and not statements and exempt:
        findings.append(Finding(rule_id="psc_exempt", route=Route.NOTE,
                                text="Exempt from the PSC register (usually a listed company).", sources=src))
    elif not pscs and not statements:
        findings.append(Finding(rule_id="no_psc", route=Route.REFER,
                                text="No person with significant control and no statement explaining why.",
                                sources=src))
    for s in statements:
        kind = s.get("statement", "")
        if kind in ("no-individual-or-entity-with-signficant-control",
                    "no-individual-or-entity-with-significant-control"):
            findings.append(Finding(rule_id="psc_none_statement", route=Route.NOTE,
                                    text="The company states nobody has significant control.", sources=src))
        elif kind:
            findings.append(Finding(rule_id="psc_statement", route=Route.REFER,
                                    text=f"PSC statement: {kind.replace('-', ' ')}.", sources=src))
    for p in pscs:
        kind = p.get("kind", "")
        name = p.get("name", "")
        if kind.startswith("corporate-entity"):
            ident = p.get("identification") or {}
            country = (ident.get("country_registered") or ident.get("place_registered") or "").strip()
            low = country.lower()
            parent = uk_parent_number(p)
            if country and low not in UK_COUNTRIES and not parent:
                secrecy = low in SECRECY_JURISDICTIONS
                findings.append(Finding(
                    rule_id="psc_secrecy_jurisdiction" if secrecy else "psc_overseas_corporate",
                    route=Route.REFER if secrecy else Route.NOTE,
                    text=f"Owned or controlled by {name}, registered in {country}." +
                         (" Ownership there is hard to see; ask who owns it." if secrecy else ""), sources=src))
                continue
            owners = data.parent_pscs.get(parent or "")
            if isinstance(owners, dict):
                names = [o.get("name", "") for o in owners.get("items") or [] if not o.get("ceased_on")]
                findings.append(Finding(rule_id="psc_uk_parent", route=Route.NOTE,
                                        text=f"Controlled by {name} ({parent}), whose own PSCs are: "
                                             + (", ".join(names[:4]) or "none listed") + ".",
                                        sources=[_ch(parent, "/persons-with-significant-control")]))
            else:
                findings.append(Finding(rule_id="psc_uk_corporate", route=Route.NOTE,
                                        text=f"Controlled by {name}" + (f" ({country})" if country else "") +
                                             ". Its owners weren't looked up" +
                                             ("" if parent else " (no UK company number given)") + ".",
                                        sources=src))
        elif kind.startswith("legal-person"):
            findings.append(Finding(rule_id="psc_legal_person", route=Route.NOTE,
                                    text=f"Controlled by legal person {name}.", sources=src))
        elif "super-secure" in kind:
            findings.append(Finding(rule_id="psc_super_secure", route=Route.REFER,
                                    text="A PSC's details are protected (super-secure). Ask the broker.",
                                    sources=src))
    people = [p.get("name", "") for p in pscs if p.get("kind", "").startswith("individual")]
    clear = (f"{len(pscs)} active PSC(s)" + (f": {', '.join(people[:3])}" if people else "") + ".")
    return _finish("ownership", title, findings, clear, src, critical=False)


# ---------------------------------------------------------------------------
# 10. Sanctions
# ---------------------------------------------------------------------------

def names_to_screen(data: CompanyData) -> list[Screened]:
    out = [Screened(name=data.name, role="company", is_person=False)]
    for prev in (data.profile.get("previous_company_names") or [])[:3]:
        out.append(Screened(name=prev.get("name", ""), role="previous name", is_person=False))
    for o in officers_in_scope(data):
        if is_corporate(o):
            out.append(Screened(name=o.get("name", ""), role="corporate director", is_person=False))
        else:
            y, m = _dob_from(o.get("date_of_birth"))
            out.append(Screened(name=o.get("name", ""), role="director", is_person=True, dob_year=y, dob_month=m))
    for p in active_pscs(data):
        if p.get("kind", "").startswith("individual"):
            y, m = _dob_from(p.get("date_of_birth"))
            out.append(Screened(name=p.get("name", ""), role="PSC", is_person=True, dob_year=y, dob_month=m))
        elif p.get("name"):
            out.append(Screened(name=p.get("name", ""), role="PSC (company)", is_person=False))
    for parent, owners in data.parent_pscs.items():
        if not isinstance(owners, dict):
            continue
        for o in owners.get("items") or []:
            if o.get("ceased_on"):
                continue
            person = o.get("kind", "").startswith("individual")
            y, m = _dob_from(o.get("date_of_birth")) if person else (None, None)
            out.append(Screened(name=o.get("name", ""), role=f"owner of parent {parent}", is_person=person,
                                dob_year=y, dob_month=m))
    return [s for s in out if s.name]


def check_sanctions(data: CompanyData, sl: SanctionsList | SourceError | None) -> CheckResult:
    title = "Sanctions (UK Sanctions List)"
    src = [SourceRef(label="UK Sanctions List (FCDO)", url=SANCTIONS_URL)]
    if not isinstance(sl, SanctionsList):
        return _couldnt("sanctions", title, _err_text(sl) if sl is not None else "the list isn't loaded", True, src)
    screened = names_to_screen(data)
    missing = [w for w, v in (("directors", data.officers), ("PSCs", data.pscs))
               if not isinstance(v, dict) and v is not NOT_FOUND]
    hits = screen(screened, sl)
    findings = []
    for h in hits:
        who = f"{h.screened.name} ({h.screened.role})"
        listed = f"listed as {h.matched_name}, ID {h.entry.unique_id}" + (f", {h.entry.regime}" if h.entry.regime else "")
        if h.strength == "strong":
            findings.append(Finding(rule_id="sanctions_strong", route=Route.COMPLIANCE_HOLD,
                                    text=f"{who}: {h.reason}; {listed}. Don't bind or pay; refer to compliance.",
                                    sources=src))
        elif h.strength == "possible":
            findings.append(Finding(rule_id="sanctions_possible", route=Route.REFER,
                                    text=f"{who}: {h.reason.lower()}; {listed}. Hold cover until cleared.",
                                    sources=src))
        else:
            findings.append(Finding(rule_id="sanctions_discounted", route=Route.NOTE,
                                    text=f"{who}: {h.reason}. Discounted on date of birth; confirm.",
                                    sources=src))
    base = (f"{len(screened)} name{'' if len(screened) == 1 else 's'} screened (company, previous names, directors, PSCs, parent owners) "
            f"against {sl.size:,} list entries; list fetched {sl.fetched_at}.")
    if missing:
        result = _couldnt("sanctions", title,
                          f"{' and '.join(missing)} couldn't be read, so only {len(screened)} name{'' if len(screened) == 1 else 's'} {'was' if len(screened) == 1 else 'were'} screened",
                          True, src)
        result.findings = findings
        return result
    return _finish("sanctions", title, findings, base + (" No matches." if not hits else ""), src, critical=True)


# ---------------------------------------------------------------------------
# 11. FCA authorisation (only when relevant)
# ---------------------------------------------------------------------------

def fca_applicable(data: CompanyData, req: PreScreenRequest) -> bool:
    sics = data.profile.get("sic_codes") or []
    return (req.expects_fca_authorisation or "financial_institutions" in req.cover_lines
            or any(s.startswith(REGULATED_SIC_PREFIXES) for s in sics))


FCA_AUTHORISED = {"authorised", "eea authorised", "registered", "eea registered",
                  "authorised (temporary permission)", "temporary permission regime"}


def best_fca_match(firms: list[dict[str, str]], name: str) -> dict[str, str] | None:
    """Several Register records can share a name (live: 'Revolut Ltd' is both an appointed representative
    and the authorised EMI). Prefer an authorised one."""
    target = normalise_name(name)
    exact = [f for f in firms if normalise_name(f["name"]) == target]
    exact.sort(key=lambda f: f["status"].lower() not in FCA_AUTHORISED)
    return exact[0] if exact else None


def check_fca(data: CompanyData, req: PreScreenRequest, firms: Any, discipline: Any) -> CheckResult:
    title = "FCA authorisation"
    src = [SourceRef(label="FCA Register", url="https://register.fca.org.uk/s/")]
    if not fca_applicable(data, req):
        return CheckResult(check_id="fca", title=title, status=CheckStatus.NOT_APPLICABLE,
                           summary="Not a regulated trade by SIC code, and not marked as needing authorisation.",
                           sources=src)
    if isinstance(firms, SourceError) or firms is None:
        r = _couldnt("fca", title, _err_text(firms), False, src)
        r.findings = [Finding(rule_id="fca_unchecked", route=Route.REFER,
                              text="Regulated trade, but FCA authorisation couldn't be checked. Ask for the FRN.",
                              sources=src)]
        return r
    firm = best_fca_match(firms, data.name)
    if firm is None:
        return _finish("fca", title, [Finding(
            rule_id="fca_not_found", route=Route.REFER,
            text="Regulated trade, but no FCA Register firm has this exact name. It may trade under another name "
                 "or be an appointed representative. Ask for the FRN.",
            detail={"register_names": [f"{f['name']} (FRN {f['frn']}, {f['status'] or 'status not given'})"
                                       for f in firms[:5]]},
            sources=src)], "", src, critical=False)
    fsrc = [SourceRef(label=f"FCA Register FRN {firm['frn']}",
                      url=f"https://register.fca.org.uk/s/search?q={firm['frn']}&type=Companies")]
    findings = []
    status = firm["status"].lower()
    authorised = status in FCA_AUTHORISED
    if authorised:
        findings.append(Finding(rule_id="fca_authorised", route=Route.NOTE,
                                text=f"{firm['name']}, FRN {firm['frn']}: {firm['status']}. "
                                     "Confirm the FRN against the proposal.", sources=fsrc))
    else:
        findings.append(Finding(rule_id="fca_not_authorised", route=Route.REFER,
                                text=f"{firm['name']}, FRN {firm['frn']}: status '{firm['status']}'.", sources=fsrc))
    if isinstance(discipline, list) and discipline:
        recent_cut = _years_ago(data.today, T["fca_discipline_years"])
        recent = []
        for line in discipline:
            dates = [_parse_date(x) for x in re.findall(r"\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}", line)]
            dates = [d for d in dates if d]
            if not dates or max(dates) >= recent_cut:
                recent.append(line)
        if recent:
            financial = req.financial_lines or not req.cover_lines
            short = [_short_discipline(line) for line in recent[:3]]
            findings.append(Finding(
                rule_id="fca_discipline", route=Route.REFER if financial else Route.NOTE,
                text=f"FCA action in the last {T['fca_discipline_years']} years: " + "; ".join(t for t, _ in short) +
                     ("." if financial else ". Relevant mainly for PI, D&O and financial institutions cover."),
                detail={"actions": recent[:5]},
                sources=fsrc + [SourceRef(label="FCA final notice", url=u) for _, u in short if u]))
    elif isinstance(discipline, SourceError):
        findings.append(Finding(rule_id="fca_discipline_unchecked", route=Route.NOTE,
                                text=f"FCA disciplinary history couldn't be read ({discipline.plain}).", sources=fsrc))
    return _finish("fca", title, findings, "", fsrc, critical=False)


# ---------------------------------------------------------------------------
# 12. Sources this version doesn't cover (shown, never hidden)
# ---------------------------------------------------------------------------

def check_ccj() -> CheckResult:
    return CheckResult(
        check_id="ccj", title="County court judgments", status=CheckStatus.COULDNT_CHECK,
        summary="Not checked: CCJ data is paid (Registry Trust or a credit reference agency) and isn't connected.",
        not_checked_reason="paid source not connected", out_of_scope=True,
        sources=[SourceRef(label="TrustOnline", url="https://www.trustonline.org.uk/")])
