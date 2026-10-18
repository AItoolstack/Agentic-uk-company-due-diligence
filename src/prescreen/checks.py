"""
The deterministic checks. Each takes the fetched data and returns a CheckResult.

Plain functions over plain data: no model calls, no network. That keeps every
flag explainable ("this field, this value, this rule") and testable on real
Companies House responses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from src.prescreen.identity import normalise_name
from src.prescreen.models import CheckResult, CheckStatus, Finding, Route, SourceRef
from src.prescreen.rules import THRESHOLDS as T
from src.prescreen.sources.companies_house import PUBLIC_SITE, public_url
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


def status_words(status: str) -> str:
    return STATUS_WORDS.get(status, status.replace("-", " "))


CLOSED_STATUSES = {"dissolved", "removed", "closed", "converted-closed"}
UK_COUNTRIES = {"united kingdom", "england", "wales", "scotland", "northern ireland",
                "england and wales", "great britain", "uk"}
REGULATED_SIC_PREFIXES = ("64", "65", "66")

SIC_NOTES = {
    "99999": "Dormant company",
    "74990": "Non-trading company",
    "82990": "Other business support (generic)",
    "96090": "Other service activities (generic)",
    "64191": "Banks",
    "64999": "Financial services n.e.c.",
    "66220": "Insurance agents and brokers",
    "64921": "Credit granting by non-deposit taking institutions",
    "68100": "Buying and selling of own real estate",
}


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
    appointments: dict[str, Any] = field(default_factory=dict)   # officer_id -> response
    disqualified: dict[str, Any] = field(default_factory=dict)   # name -> search response
    disq_details: dict[str, Any] = field(default_factory=dict)   # self link -> detail

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
        return None


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


def _couldnt(check_id: str, title: str, reason: str, critical: bool, sources=None) -> CheckResult:
    return CheckResult(check_id=check_id, title=title, status=CheckStatus.COULDNT_CHECK,
                       summary=f"Couldn't check: {reason}", not_checked_reason=reason,
                       critical=critical, sources=sources or [])


def _err_text(value: Any) -> str:
    return value.plain if isinstance(value, SourceError) else "no response"


def _finish(check_id: str, title: str, findings: list[Finding], clear_summary: str,
            sources: list[SourceRef], critical: bool) -> CheckResult:
    flagged = [f for f in findings if f.route != Route.NOTE]
    if flagged:
        status, summary = CheckStatus.FLAG, "; ".join(f.text for f in flagged)
    elif findings:
        status, summary = CheckStatus.CLEAR, "Nothing that changes the route. " + "; ".join(f.text for f in findings)
    else:
        status, summary = CheckStatus.CLEAR, clear_summary
    return CheckResult(check_id=check_id, title=title, status=status, summary=summary,
                       findings=findings, sources=sources, critical=critical)


def active_officers(data: CompanyData) -> list[dict[str, Any]]:
    if not isinstance(data.officers, dict):
        return []
    return [o for o in data.officers.get("items") or []
            if not o.get("resigned_on") and o.get("officer_role", "").endswith("director")]


def officer_id(officer: dict[str, Any]) -> str | None:
    path = ((officer.get("links") or {}).get("officer") or {}).get("appointments", "")
    m = re.search(r"/officers/([^/]+)/appointments", path)
    return m.group(1) if m else None


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
        findings.append(Finding(rule_id="strike_off_proposed", route=Route.OUTSIDE_APPETITE,
                                text="Active proposal to strike off (First Gazette notice). Draft rule: outside appetite "
                                     "unless the broker shows an objection has been filed.",
                                sources=[_ch(n, "/filing-history")]))
    if p.get("registered_office_is_in_dispute"):
        findings.append(Finding(rule_id="office_disputed", route=Route.REFER,
                                text="Registered office address is in dispute.", sources=src))
    if p.get("undeliverable_registered_office_address"):
        findings.append(Finding(rule_id="office_undeliverable", route=Route.REFER,
                                text="Post can't be delivered to the registered office.", sources=src))
    return _finish("status", "Company status", findings, "Active, no strike-off proposal, registered office fine.",
                   src, critical=True)


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
        findings.append(Finding(rule_id="company_new", route=Route.REFER,
                                text=f"Incorporated {created:%d %b %Y}, {months} months ago: no trading history. "
                                     "Ask about any predecessor business.",
                                detail={"months": months}, sources=src))
    elif months < T["young_company_months"]:
        findings.append(Finding(rule_id="company_young", route=Route.NOTE,
                                text=f"Incorporated {created:%d %b %Y} ({months} months).",
                                detail={"months": months}, sources=src))

    for prev in p.get("previous_company_names") or []:
        ceased = _parse_date(prev.get("ceased_on"))
        if ceased and _months_between(ceased, data.today) < T["rename_recent_months"]:
            findings.append(Finding(
                rule_id="renamed_recently", route=Route.NOTE,
                text=f"Renamed from {prev.get('name')} on {ceased:%d %b %Y}. News about the current name from "
                     "before that date is about a different company.",
                detail={"previous_name": prev.get("name"), "ceased_on": prev.get("ceased_on")}, sources=src))
    summary_age = f"Incorporated {created:%d %b %Y} ({months // 12} years)."
    result = _finish("age", "Age and name history", findings, summary_age, src, critical=False)
    if result.status == CheckStatus.CLEAR and not findings:
        result.summary = summary_age
    return result


# ---------------------------------------------------------------------------
# 3. Filings
# ---------------------------------------------------------------------------

def check_filings(data: CompanyData) -> CheckResult:
    p, n = data.profile, data.number
    src = [_ch(n, "/filing-history")]
    accounts = p.get("accounts") or {}
    nxt = accounts.get("next_accounts") or {}
    cs = p.get("confirmation_statement") or {}
    if not accounts and not cs:
        return _couldnt("filings", "Filing compliance", "no accounts or confirmation statement data", True, src)

    findings: list[Finding] = []
    overdue = nxt.get("overdue", accounts.get("overdue"))
    due = _parse_date(nxt.get("due_on") or accounts.get("next_due"))
    if overdue:
        days = (data.today - due).days if due else None
        text = "Accounts are overdue" + (f" by {days} days (due {due:%d %b %Y})." if days is not None else ".")
        findings.append(Finding(rule_id="accounts_overdue", route=Route.REFER, text=text,
                                detail={"days_overdue": days}, sources=src))
        if days is not None and days > T["overdue_serious_days"]:
            findings.append(Finding(rule_id="accounts_overdue_serious", route=Route.REFER,
                                    text=f"Accounts more than {T['overdue_serious_days']} days overdue.", sources=src))
    if cs.get("overdue"):
        cs_due = _parse_date(cs.get("next_due"))
        findings.append(Finding(rule_id="confirmation_overdue", route=Route.REFER,
                                text="Confirmation statement is overdue" +
                                     (f" (was due {cs_due:%d %b %Y})." if cs_due else "."), sources=src))
    last_type = (accounts.get("last_accounts") or {}).get("type", "")
    if last_type == "dormant" or "99999" in (p.get("sic_codes") or []):
        findings.append(Finding(rule_id="dormant", route=Route.NOTE,
                                text="Filed as dormant / SIC 99999. If the proposal says it trades, ask why.",
                                sources=src))
    clear = "Accounts and confirmation statement up to date" + (
        f"; next accounts due {due:%d %b %Y}." if due else ".")
    return _finish("filings", "Filing compliance", findings, clear, src, critical=True)


# ---------------------------------------------------------------------------
# 4. Insolvency history
# ---------------------------------------------------------------------------

def check_insolvency(data: CompanyData) -> CheckResult:
    n = data.number
    src = [_ch(n, "/insolvency")]
    links = data.profile.get("links") or {}
    has_link = "insolvency" in links or bool(data.profile.get("has_insolvency_history"))
    if not has_link:
        return CheckResult(check_id="insolvency", title="Insolvency history", status=CheckStatus.CLEAR,
                           summary="No insolvency record on Companies House.", sources=src, critical=True)
    ins = data.insolvency
    if isinstance(ins, SourceError) or ins is None:
        return _couldnt("insolvency", "Insolvency history",
                        f"the company has an insolvency record but it couldn't be read ({_err_text(ins)})", True, src)
    if ins is NOT_FOUND:
        return _couldnt("insolvency", "Insolvency history",
                        "the profile links to an insolvency record but it returned not found", True, src)
    cases = ins.get("cases") or []
    findings = []
    for case in cases:
        kind = (case.get("type") or "insolvency case").replace("-", " ")
        dates = ", ".join(f"{d.get('type', '').replace('-', ' ')} {d.get('date', '')}" for d in case.get("dates") or [])
        findings.append(Finding(rule_id="insolvency_history", route=Route.REFER,
                                text=f"Insolvency case on record: {kind}" + (f" ({dates})" if dates else "") + ".",
                                detail=case, sources=src))
    if not findings:
        findings.append(Finding(rule_id="insolvency_history", route=Route.REFER,
                                text="Companies House shows insolvency history; open the record.", sources=src))
    return _finish("insolvency", "Insolvency history", findings, "", src, critical=True)


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
    if ch is NOT_FOUND:
        return CheckResult(check_id="charges", title="Charges", status=CheckStatus.CLEAR,
                           summary="No charges returned by Companies House.", sources=src)
    if isinstance(ch, SourceError) or ch is None:
        return _couldnt("charges", "Charges", f"charges couldn't be read ({_err_text(ch)})", False, src)
    items = ch.get("items") or []
    outstanding = [c for c in items if c.get("status") in ("outstanding", "part-satisfied")]
    recent = [c for c in items
              if (d := _parse_date(c.get("created_on"))) and _months_between(d, data.today) < T["charge_burst_months"]]
    findings = []
    if len(recent) >= T["charge_burst_count"]:
        findings.append(Finding(rule_id="charges_burst", route=Route.REFER,
                                text=f"{len(recent)} new charges in the last {T['charge_burst_months']} months "
                                     "(draft rule: can mean lender pressure).", sources=src))
    if outstanding:
        lenders = sorted({p for c in outstanding for p in
                          [x.get("name", "") for x in c.get("persons_entitled") or []] if p})
        findings.append(Finding(rule_id="charges_outstanding", route=Route.NOTE,
                                text=f"{len(outstanding)} outstanding charge(s)" +
                                     (f" to {', '.join(lenders[:4])}" if lenders else "") +
                                     ". Check the loss payee on property cover.",
                                detail={"lenders": lenders}, sources=src))
    return _finish("charges", "Charges", findings, "All registered charges are satisfied.", src, critical=False)


# ---------------------------------------------------------------------------
# 6. Directors: disqualification
# ---------------------------------------------------------------------------

def _name_tokens(name: str) -> frozenset[str]:
    s = re.sub(r"[^A-Za-z ]+", " ", (name or "").upper())
    return frozenset(t for t in s.split() if len(t) > 1 and t not in {"MR", "MRS", "MS", "DR", "SIR"})


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
    directors = active_officers(data)[: T["max_directors_checked"]]
    if not directors:
        return CheckResult(check_id="directors_disqualified", title=title, status=CheckStatus.FLAG,
                           summary="No active directors on the register.", critical=True,
                           findings=[Finding(rule_id="no_directors", route=Route.REFER,
                                             text="No active directors on the register.",
                                             sources=[_ch(n, "/officers")])])
    src = [SourceRef(label="Companies House disqualified directors register",
                     url=f"{PUBLIC_SITE}/search/disqualified-officers")]
    findings, failed = [], []
    for d in directors:
        name = d.get("name", "")
        res = data.disqualified.get(name)
        if isinstance(res, SourceError) or res is None:
            failed.append(name)
            continue
        hits = [] if res is NOT_FOUND else res.get("items") or []
        y, m = _dob_from(d.get("date_of_birth"))
        mine = _name_tokens(name)
        for h in hits:
            if _name_tokens(h.get("title", "")) != mine:
                continue
            hy, hm = _dob_from(h.get("date_of_birth"))
            link = (h.get("links") or {}).get("self", "")
            hit_src = [SourceRef(label="Disqualification record", url=f"{PUBLIC_SITE}{link}" if link else src[0].url)]
            if y and hy and (y != hy or (m and hm and m != hm)):
                findings.append(Finding(rule_id="disq_name_only", route=Route.NOTE,
                                        text=f"{name}: a disqualified person has the same name, but a different "
                                             f"date of birth ({hm}/{hy} vs {m}/{y}). Not the same person.",
                                        sources=hit_src))
                continue
            if not (y and hy):
                findings.append(Finding(rule_id="disq_possible", route=Route.REFER,
                                        text=f"{name}: same name as a disqualified person; no date of birth to "
                                             "confirm. Check the record.", sources=hit_src))
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
                text=f"{name} matches a disqualified director (name and date of birth)" +
                     (f", disqualified until {until}." if until else "."),
                sources=hit_src))
    if failed:
        result = _couldnt("directors_disqualified", title,
                          f"the register search failed for {len(failed)} of {len(directors)} directors", True, src)
        result.findings = findings
        return result
    return _finish("directors_disqualified", title, findings,
                   f"{len(directors)} active director(s) checked by name and date of birth; none disqualified.",
                   src, critical=True)


# ---------------------------------------------------------------------------
# 7. Directors: links to insolvent companies (phoenix pattern)
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


def check_directors_insolvency(data: CompanyData) -> CheckResult:
    title = "Directors: other companies"
    n = data.number
    if not isinstance(data.officers, dict):
        return _couldnt("directors_insolvency", title,
                        f"the officer list couldn't be read ({_err_text(data.officers)})", True, [_ch(n, "/officers")])
    directors = active_officers(data)[: T["max_directors_checked"]]
    created = _parse_date(data.profile.get("date_of_creation"))
    young = created is not None and _months_between(created, data.today) < T["phoenix_young_months"]
    cutoff_year = data.today.year - T["director_lookback_years"]
    findings, failed, dissolved_note = [], [], []
    src = []

    for d in directors:
        name, oid = d.get("name", ""), officer_id(d)
        if not oid:
            failed.append(name)
            continue
        res = data.appointments.get(oid)
        if isinstance(res, SourceError) or res is None or res is NOT_FOUND:
            failed.append(name)
            continue
        ref = SourceRef(label=f"{name}: appointments", url=f"{PUBLIC_SITE}/officers/{oid}/appointments")
        src.append(ref)
        insolvent, dissolved = [], 0
        for a in res.get("items") or []:
            to = a.get("appointed_to") or {}
            if to.get("company_number") == n:
                continue
            resigned = _parse_date(a.get("resigned_on"))
            if resigned and resigned.year < cutoff_year:
                continue
            st = to.get("company_status", "")
            if st in INSOLVENT_STATUSES:
                insolvent.append(to)
            elif st == "dissolved":
                dissolved += 1
        if dissolved:
            dissolved_note.append(f"{name}: {dissolved}")
        if not insolvent:
            continue
        names = ", ".join(f"{c.get('company_name')} ({c.get('company_number')}, "
                          f"{status_words(c.get('company_status', ''))})" for c in insolvent[:4])
        predecessor = [c for c in insolvent if _similar_names(c.get("company_name", ""), data.name)]
        if predecessor and young:
            findings.append(Finding(
                rule_id="phoenix_pattern", route=Route.OUTSIDE_APPETITE,
                text=f"{name} was a director of {predecessor[0].get('company_name')}, which has a similar name and is "
                     f"{status_words(predecessor[0].get('company_status', ''))}; this company is under "
                     f"{T['phoenix_young_months']} months old. Possible phoenix (and s.216 name re-use).",
                sources=[ref]))
        count = len(insolvent)
        if count >= T["director_insolvent_decline"]:
            route = Route.OUTSIDE_APPETITE
        elif count >= T["director_insolvent_refer"]:
            route = Route.REFER
        else:
            continue
        findings.append(Finding(
            rule_id="director_insolvent_links", route=route,
            text=f"{name} is or was (in the last {T['director_lookback_years']} years) a director of {count} "
                 f"insolvent compan{'y' if count == 1 else 'ies'}: {names}.",
            detail={"count": count}, sources=[ref]))

    if dissolved_note:
        findings.append(Finding(
            rule_id="director_dissolved_context", route=Route.NOTE,
            text="Dissolved companies (not counted against anyone; closing dormant companies is normal): "
                 + "; ".join(dissolved_note) + "."))
    if failed:
        result = _couldnt("directors_insolvency", title,
                          f"appointment history couldn't be read for {len(failed)} of {len(directors)} directors",
                          True, src)
        result.findings = findings
        return result
    return _finish("directors_insolvency", title, findings,
                   f"{len(directors)} active director(s): no links to insolvent companies in the last "
                   f"{T['director_lookback_years']} years.", src, critical=True)


# ---------------------------------------------------------------------------
# 8. Ownership (PSC)
# ---------------------------------------------------------------------------

def active_pscs(data: CompanyData) -> list[dict[str, Any]]:
    if not isinstance(data.pscs, dict):
        return []
    return [p for p in data.pscs.get("items") or [] if not p.get("ceased_on") and not p.get("ceased")]


def check_ownership(data: CompanyData) -> CheckResult:
    title = "Ownership (PSC register)"
    n = data.number
    src = [_ch(n, "/persons-with-significant-control")]
    if isinstance(data.pscs, SourceError) or data.pscs is None:
        return _couldnt("ownership", title, f"the PSC register couldn't be read ({_err_text(data.pscs)})", False, src)
    pscs = active_pscs(data)
    statements = []
    if isinstance(data.psc_statements, dict):
        statements = [s for s in data.psc_statements.get("items") or [] if not s.get("ceased_on")]
    findings = []
    if not pscs and not statements:
        findings.append(Finding(rule_id="no_psc", route=Route.REFER,
                                text="No person with significant control and no statement explaining why.",
                                sources=src))
    for s in statements:
        kind = s.get("statement", "")
        if kind in ("no-individual-or-entity-with-signficant-control",
                    "no-individual-or-entity-with-significant-control"):
            findings.append(Finding(rule_id="psc_none_statement", route=Route.NOTE,
                                    text="Company states nobody has significant control.", sources=src))
        elif kind:
            findings.append(Finding(rule_id="psc_statement", route=Route.REFER,
                                    text=f"PSC statement: {kind.replace('-', ' ')}.", sources=src))
    for p in pscs:
        kind = p.get("kind", "")
        name = p.get("name", "")
        if kind.startswith("corporate-entity"):
            ident = p.get("identification") or {}
            country = (ident.get("country_registered") or ident.get("place_registered") or "").strip()
            if country and country.lower() not in UK_COUNTRIES:
                findings.append(Finding(rule_id="psc_overseas_corporate", route=Route.REFER,
                                        text=f"Owned or controlled by {name}, registered in {country}. "
                                             "Ask who owns it.", sources=src))
            else:
                findings.append(Finding(rule_id="psc_uk_corporate", route=Route.NOTE,
                                        text=f"Controlled by {name}" + (f" ({country})" if country else "") +
                                             ". Its own PSC register shows the owners.", sources=src))
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
# 9. Sanctions
# ---------------------------------------------------------------------------

def names_to_screen(data: CompanyData) -> list[Screened]:
    out = [Screened(name=data.name, role="company", is_person=False)]
    for prev in (data.profile.get("previous_company_names") or [])[:3]:
        out.append(Screened(name=prev.get("name", ""), role="previous name", is_person=False))
    for o in active_officers(data):
        y, m = _dob_from(o.get("date_of_birth"))
        out.append(Screened(name=o.get("name", ""), role="director", is_person=True, dob_year=y, dob_month=m))
    for p in active_pscs(data):
        if p.get("kind", "").startswith("individual"):
            y, m = _dob_from(p.get("date_of_birth"))
            out.append(Screened(name=p.get("name", ""), role="PSC", is_person=True, dob_year=y, dob_month=m))
        elif p.get("name"):
            out.append(Screened(name=p.get("name", ""), role="PSC (company)", is_person=False))
    return [s for s in out if s.name]


def check_sanctions(data: CompanyData, sl: SanctionsList | SourceError | None) -> CheckResult:
    title = "Sanctions (UK Sanctions List)"
    src = [SourceRef(label="UK Sanctions List (FCDO)", url=SANCTIONS_URL)]
    if not isinstance(sl, SanctionsList):
        return _couldnt("sanctions", title, _err_text(sl) if sl is not None else "the list isn't loaded", True, src)
    screened = names_to_screen(data)
    if not isinstance(data.officers, dict) or data.pscs is None or isinstance(data.pscs, SourceError):
        missing = [w for w, v in (("directors", data.officers), ("PSCs", data.pscs))
                   if not isinstance(v, dict) and v is not NOT_FOUND]
        if missing:
            return _couldnt("sanctions", title,
                            f"only the company name could be screened; {' and '.join(missing)} couldn't be read",
                            True, src)
    hits = screen(screened, sl)
    findings = []
    for h in hits:
        who = f"{h.screened.name} ({h.screened.role})"
        listed = f"listed as {h.matched_name}, ID {h.entry.unique_id}" + (f", {h.entry.regime}" if h.entry.regime else "")
        if h.strength == "strong":
            findings.append(Finding(rule_id="sanctions_strong", route=Route.OUTSIDE_APPETITE,
                                    text=f"{who}: {h.reason}; {listed}. Don't bind; escalate to compliance.",
                                    sources=src))
        elif h.strength == "possible":
            findings.append(Finding(rule_id="sanctions_possible", route=Route.REFER,
                                    text=f"{who}: {h.reason.lower()}; {listed}. Hold cover until cleared.",
                                    sources=src))
        else:
            findings.append(Finding(rule_id="sanctions_cleared", route=Route.NOTE,
                                    text=f"{who}: {h.reason}. Recorded as cleared.", sources=src))
    clear = (f"{len(screened)} names screened (company, previous names, directors, PSCs) against "
             f"{sl.size:,} list entries, list fetched {sl.fetched_at}. No matches.")
    result = _finish("sanctions", title, findings, clear, src, critical=True)
    if result.status == CheckStatus.CLEAR and findings:
        result.summary = f"{len(screened)} names screened against {sl.size:,} entries (fetched {sl.fetched_at}). " \
                         + result.summary
    return result


# ---------------------------------------------------------------------------
# 10. FCA authorisation (only when relevant)
# ---------------------------------------------------------------------------

def fca_applicable(data: CompanyData, expects_fca: bool) -> bool:
    sics = data.profile.get("sic_codes") or []
    return expects_fca or any(s.startswith(REGULATED_SIC_PREFIXES) for s in sics)


def check_fca(data: CompanyData, expects_fca: bool, firms: Any, discipline: Any) -> CheckResult:
    title = "FCA authorisation"
    src = [SourceRef(label="FCA Register", url="https://register.fca.org.uk/s/")]
    if not fca_applicable(data, expects_fca):
        return CheckResult(check_id="fca", title=title, status=CheckStatus.NOT_APPLICABLE,
                           summary="Not a regulated trade by SIC code, and not marked as needing authorisation.",
                           sources=src)
    if isinstance(firms, SourceError) or firms is None:
        r = _couldnt("fca", title, _err_text(firms), False, src)
        r.findings = [Finding(rule_id="fca_unchecked", route=Route.REFER,
                              text="Regulated trade, but FCA authorisation couldn't be checked. Ask for the FRN.",
                              sources=src)]
        return r
    target = normalise_name(data.name)
    exact = [f for f in firms if normalise_name(f["name"]) == target]
    if not exact:
        return _finish("fca", title, [Finding(
            rule_id="fca_not_found", route=Route.REFER,
            text="Regulated trade, but no FCA Register firm has this exact name. It may trade under another name "
                 "or be an appointed representative. Ask for the FRN.", sources=src)], "", src, critical=False)
    firm = exact[0]
    fsrc = [SourceRef(label=f"FCA Register FRN {firm['frn']}",
                      url=f"https://register.fca.org.uk/s/search?q={firm['frn']}&type=Companies")]
    findings = []
    status = firm["status"].lower()
    if "authorised" in status and "no longer" not in status or status == "registered":
        findings.append(Finding(rule_id="fca_authorised", route=Route.NOTE,
                                text=f"FCA Register: {firm['name']}, FRN {firm['frn']}, {firm['status']}. "
                                     "Confirm the FRN against the proposal.", sources=fsrc))
    else:
        findings.append(Finding(rule_id="fca_not_authorised", route=Route.REFER,
                                text=f"FCA Register: {firm['name']}, FRN {firm['frn']}, status '{firm['status']}'.",
                                sources=fsrc))
    if isinstance(discipline, list) and discipline:
        findings.append(Finding(rule_id="fca_discipline", route=Route.REFER,
                                text=f"FCA disciplinary history ({len(discipline)}): " + "; ".join(discipline[:3]),
                                sources=fsrc))
    elif isinstance(discipline, SourceError):
        findings.append(Finding(rule_id="fca_discipline_unchecked", route=Route.NOTE,
                                text=f"FCA disciplinary history couldn't be read ({discipline.plain}).", sources=fsrc))
    return _finish("fca", title, findings, "", fsrc, critical=False)


# ---------------------------------------------------------------------------
# 11. Sources this version doesn't cover (shown, never hidden)
# ---------------------------------------------------------------------------

def check_ccj() -> CheckResult:
    return CheckResult(
        check_id="ccj", title="County court judgments", status=CheckStatus.COULDNT_CHECK,
        summary="Not checked: CCJ data is paid (Registry Trust / credit reference agencies) and isn't connected.",
        not_checked_reason="paid source not connected",
        sources=[SourceRef(label="TrustOnline", url="https://www.trustonline.org.uk/")])
