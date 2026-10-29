"""
Runs one pre-screen: fetch everything for one company number, run the checks,
apply the rules, write the report.

Order matters in three places: identity comes first (nothing runs without a
number); director lookups need the officer list; and dissolved companies in a
director's history need their own profile to see if they went through
insolvency. Everything else is fetched in parallel.

The invariant every check keeps: CLEAR only when coverage was complete.
A source failure, a cap, a truncated page or an unread result is
"couldn't check", and an unexpected bug in a check is too.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable

from src.prescreen import casefile
from src.prescreen import checks as C
from src.prescreen.llm import JSONChat
from src.prescreen.media import check_media
from src.prescreen import caseview
from src.prescreen.models import CheckResult, CheckStatus, PreScreenReport, PreScreenRequest, Route, utcnow
from src.prescreen.rules import RULES_VERSION, decide
from src.prescreen.sources.companies_house import CompaniesHouse
from src.prescreen.sources.fca import FCARegister
from src.prescreen.sources.gazette import Gazette
from src.prescreen.sources.http import NOT_FOUND, SourceError
from src.prescreen.sources.news import NewsSearch
from src.prescreen.sic import label as sic_label
from src.prescreen.sources.sanctions import SanctionsList


class CompanyNotFound(Exception):
    pass


class SourceDown(Exception):
    pass


class NotCovered(CompanyNotFound):
    """The number is on Companies House, but it isn't a company this tool can pre-screen. A subclass of
    CompanyNotFound so every caller already handles it (no report, the run is refunded)."""


# Companies House registers these but doesn't hold their officers, owners or accounts, so a pre-screen built on
# Companies House would read their empty officer and PSC lists as findings. Say so instead.
NOT_COVERED_TYPES = {
    "industrial-and-provident-society": "a registered society",
    "registered-society-non-jurisdictional": "a registered society",
}


@dataclass
class Services:
    ch: CompaniesHouse
    fca: FCARegister
    news: NewsSearch
    llm: JSONChat
    sanctions: Any  # async callable returning SanctionsList, or a SanctionsList / SourceError
    gazette: Gazette | None = None
    today: date | None = None


async def _safe(coro: Awaitable[Any]) -> Any:
    try:
        return await coro
    except SourceError as err:
        return err


async def _none() -> None:
    return None


async def _sanctions(services: Services) -> SanctionsList | SourceError:
    s = services.sanctions
    if isinstance(s, (SanctionsList, SourceError)):
        return s
    try:
        return await s()
    except SourceError as err:
        return err


def _guard(check_id: str, title: str, fn: Callable[[], CheckResult], critical: bool = False) -> CheckResult:
    """A bug in one check becomes "couldn't check" for that check, not a failed report."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - deliberately broad: shown to the user
        return CheckResult(check_id=check_id, title=title, status=CheckStatus.COULDNT_CHECK,
                           summary=f"Couldn't check: internal error ({type(exc).__name__}).",
                           not_checked_reason=f"internal error ({type(exc).__name__})", critical=critical)


ROUTE_WORDS = {
    Route.NOTE: "Note only",
    Route.REFER: "Refer",
    Route.OUTSIDE_APPETITE: "Outside appetite",
    Route.COMPLIANCE_HOLD: "Compliance hold",
}


def rules_summary(route: Route, reasons: list[str], checks: list[CheckResult]) -> str:
    cov = casefile.coverage(checks)
    flagged = sum(1 for c in checks if c.status == CheckStatus.FLAG)
    parts = [f"{ROUTE_WORDS[route]}. {cov['text']} {flagged} flagged."]
    if reasons:
        parts.append("Main reason: " + reasons[0])
    parts.append("A recommendation only; the underwriter decides.")
    return " ".join(parts)


Progress = Callable[[str, str, str], None]

STAGES = [
    ("companies_house", "Companies House: profile, officers, owners, charges"),
    ("gazette", "The Gazette: insolvency notices"),
    ("sanctions", "UK Sanctions List"),
    ("directors", "Directors: disqualification and other companies"),
    ("fca", "FCA Register"),
    ("news", "News and web, read against the company"),
]


def company_card(data: C.CompanyData) -> dict[str, Any]:
    p = data.profile
    addr = p.get("registered_office_address") or {}
    last = (p.get("accounts") or {}).get("last_accounts") or {}
    return {
        "status": p.get("company_status", ""),
        "status_detail": p.get("company_status_detail", ""),
        "type": p.get("type", ""),
        "jurisdiction": p.get("jurisdiction", ""),
        "incorporated": p.get("date_of_creation", ""),
        "registered_office": ", ".join(v for v in (addr.get("premises"), addr.get("address_line_1"),
                                                   addr.get("address_line_2"), addr.get("locality"),
                                                   addr.get("postal_code")) if v),
        "sic": [{"code": c, "label": sic_label(c)} for c in p.get("sic_codes") or []],
        "previous_names": [{"name": x.get("name"), "until": x.get("ceased_on")}
                           for x in p.get("previous_company_names") or []][:3],
        "last_accounts": {"type": last.get("type", ""), "made_up_to": last.get("period_end_on") or last.get("made_up_to", "")},
        "officers": [{"name": o.get("name"), "role": o.get("officer_role", "").replace("-", " ")}
                     for o in C.active_officers(data)][:8],
        "officer_count": len(C.active_officers(data)),
        "owners": [p2.get("name") for p2 in C.active_pscs(data)][:4],
        # Full lists, for the daily re-check to compare against.
        "officer_names": sorted(o.get("name", "") for o in C.active_officers(data)),
        "owner_names": sorted(p2.get("name", "") for p2 in C.active_pscs(data)),
        "officers_read": isinstance(data.officers, dict),
        # For the case page's People tab: everyone checked (current, and those who left in the last 12 months),
        # and the owners, with dates. No dates of birth.
        "people": [{"name": o.get("name", ""), "role": (o.get("officer_role") or "").replace("-", " "),
                    "appointed": o.get("appointed_on", ""), "resigned": o.get("resigned_on", ""),
                    "officer_id": C.officer_id(o) or ""} for o in _people(data)],
        "owners_detail": [{"name": x.get("name", ""), "kind": _psc_kind(x.get("kind", "")),
                           "notified": x.get("notified_on", ""),
                           "control": [n.replace("-", " ") for n in (x.get("natures_of_control") or [])][:3]}
                          for x in C.active_pscs(data)][:10],
        "owners_read": isinstance(data.pscs, dict),
    }


def _people(data: C.CompanyData) -> list[dict[str, Any]]:
    try:
        return C.officers_in_scope(data)[:60]
    except Exception:  # noqa: BLE001 - display only
        return []


def _psc_kind(kind: str) -> str:
    if "corporate" in kind or "legal-person" in kind:
        return "company"
    if "individual" in kind:
        return "person"
    return kind.replace("-", " ")


async def run_prescreen(req: PreScreenRequest, services: Services, progress: Progress | None = None,
                        skip_media: bool = False) -> PreScreenReport:
    """skip_media: the daily re-check doesn't re-read news (it would cost a model call per company)."""
    read_at: dict[str, dict[str, str]] = {}

    def tick(stage: str, state: str, detail: str = "") -> None:
        if state in ("done", "error", "skip"):
            read_at[stage] = {"state": state, "at": utcnow().isoformat(), "detail": detail}
        if progress:
            try:
                progress(stage, state, detail)
            except Exception:  # noqa: BLE001 - progress is cosmetic; never break a run
                pass

    t0 = time.perf_counter()
    today = services.today or date.today()
    ch = services.ch
    number = req.company_number

    profile = await _safe(ch.profile(number))
    if profile is NOT_FOUND:
        raise CompanyNotFound(f"Companies House has no company with number {number}.")
    if isinstance(profile, SourceError):
        raise SourceDown(profile.plain)
    kind = NOT_COVERED_TYPES.get(profile.get("type", ""))
    if kind:
        name = profile.get("company_name") or number
        raise NotCovered(
            f"{name} ({number}) is {kind}, not a company. Companies House doesn't hold its directors, owners or "
            "accounts, so this tool can't pre-screen it; nothing was checked. The FCA Mutuals Public Register "
            "(https://mutuals.fca.org.uk) holds its records.")

    tick("companies_house", "start", profile.get("company_name", ""))
    tick("gazette", "start")
    tick("sanctions", "start")
    links = profile.get("links") or {}
    strike_off = profile.get("company_status_detail") == "active-proposal-to-strike-off"
    gazette = services.gazette
    (officers, pscs, statements, charges, insolvency, gazette_filings, notices,
     sanctions_list) = await asyncio.gather(
        _safe(ch.officers(number)),
        _safe(ch.pscs(number)),
        _safe(ch.psc_statements(number)) if "persons_with_significant_control_statements" in links else _none(),
        _safe(ch.charges(number)) if ("charges" in links or profile.get("has_charges")) else _none(),
        _safe(ch.insolvency(number)) if ("insolvency" in links or profile.get("has_insolvency_history")) else _none(),
        _safe(ch.filing_history(number, "gazette")) if strike_off else _none(),
        _safe(gazette.insolvency_notices(number, profile.get("company_name", ""))) if gazette
        else _gazette_missing(),
        _sanctions(services),
    )
    data = C.CompanyData(number=number, profile=profile, today=today, officers=officers, pscs=pscs,
                         psc_statements=statements, charges=charges, insolvency=insolvency,
                         gazette_filings=gazette_filings, gazette_notices=notices)
    tick("companies_house", "error" if isinstance(officers, SourceError) or isinstance(pscs, SourceError) else "done")
    tick("gazette", "error" if isinstance(notices, SourceError) else "done")
    tick("sanctions", "error" if isinstance(sanctions_list, SourceError) else "done",
         f"{sanctions_list.size:,} entries" if isinstance(sanctions_list, SanctionsList) else "")
    tick("directors", "start")

    # Directors (incl. those resigned in the last 12 months), then their history.
    people, _ = C.people_to_check(data)
    ids = [(d, C.officer_id(d)) for d in people]
    with_ids = [x for x in ids if x[1]]
    appts, disq = await asyncio.gather(
        asyncio.gather(*[_safe(ch.appointments(oid)) for _, oid in with_ids]),
        asyncio.gather(*[_safe(ch.search_disqualified(d.get("name", ""))) for d in people]),
    )
    data.appointments = {oid: r for (_, oid), r in zip(with_ids, appts)}
    data.disqualified = {d.get("name", ""): r for d, r in zip(people, disq)}

    linked = C.linked_companies_to_profile(data)
    detail_links = []
    for d in people:
        res = data.disqualified.get(d.get("name", ""))
        if isinstance(res, dict):
            for h in res.get("items") or []:
                if C.names_match(C._name_tokens(h.get("title", "")), C._name_tokens(d.get("name", ""))):
                    link = (h.get("links") or {}).get("self")
                    if link:
                        detail_links.append(link)
    parents = [p for p in (C.uk_parent_number(x) for x in C.active_pscs(data)) if p and p != number][:2]
    linked_profiles, details, parent_pscs = await asyncio.gather(
        asyncio.gather(*[_safe(ch.profile(num)) for num in linked]),
        asyncio.gather(*[_safe(ch.disqualified_natural(link)) for link in detail_links]),
        asyncio.gather(*[_safe(ch.pscs(p)) for p in parents]),
    )
    data.linked_profiles = dict(zip(linked, linked_profiles))
    # When each linked insolvency began: a director who left years before it can't have caused it (rules 19e).
    to_read = C.linked_insolvencies_to_read(data)
    records = await asyncio.gather(*[_safe(ch.insolvency(num)) for num in to_read])
    data.linked_insolvency = dict(zip(to_read, records))
    data.disq_details = dict(zip(detail_links, details))
    data.parent_pscs = {p: r for p, r in zip(parents, parent_pscs) if isinstance(r, dict)}
    tick("directors", "done", f"{len(people)} checked")

    # FCA only when relevant; news in parallel with it.
    fca_needed = C.fca_applicable(data, req)

    async def fca_lookup() -> tuple[Any, Any]:
        if not fca_needed:
            return None, None
        target = C.normalise_name(data.name)

        def found(row: dict) -> bool:  # keep paging until an authorised firm of this exact name turns up
            return C.normalise_name(row["name"]) == target and row["status"].lower() in C.FCA_AUTHORISED

        firms = await _safe(services.fca.search_firms(data.name, want=found))
        discipline = None
        # The Register's search is fuzzy; if the full name found nothing exact, retry once with the bare name.
        if isinstance(firms, list) and not any(C.normalise_name(f["name"]) == target for f in firms) \
                and target and target != data.name.upper().strip():
            again = await _safe(services.fca.search_firms(target.title(), want=found))
            if isinstance(again, list):
                firms = firms + again
        if isinstance(firms, list):
            best = C.best_fca_match(firms, data.name)
            if best:
                discipline = await _safe(services.fca.disciplinary_history(best["frn"]))
        return firms, discipline

    prev_names = [p.get("name", "") for p in profile.get("previous_company_names") or []]
    tick("fca", "start" if fca_needed else "skip", "" if fca_needed else "not a regulated trade")
    tick("news", "skip" if skip_media else "start", "not re-read in the daily check" if skip_media else "")

    async def news_lookup() -> Any:
        if skip_media:
            return None
        return await _safe(services.news.search(data.name, previous_names=prev_names))

    (firms, discipline), news = await asyncio.gather(fca_lookup(), news_lookup())
    if fca_needed:
        tick("fca", "error" if isinstance(firms, SourceError) else "done")

    results: list[CheckResult] = [
        _guard("status", "Company status", lambda: C.check_status(data), True),
        _guard("age", "Age and name history", lambda: C.check_age_and_names(data)),
        _guard("filings", "Filings and accounts", lambda: C.check_filings(data, req), True),
        _guard("insolvency", "Insolvency (Companies House and Gazette)", lambda: C.check_insolvency(data), True),
        _guard("charges", "Charges", lambda: C.check_charges(data)),
        _guard("directors_disqualified", "Directors: disqualification",
               lambda: C.check_directors_disqualified(data), True),
        _guard("directors_insolvency", "Directors: other companies",
               lambda: C.check_directors_insolvency(data), True),
        _guard("changes", "Board and ownership changes (12 months)", lambda: C.check_changes(data)),
        _guard("ownership", "Ownership (PSC register)", lambda: C.check_ownership(data)),
        _guard("sanctions", "Sanctions (UK Sanctions List)", lambda: C.check_sanctions(data, sanctions_list), True),
        _guard("fca", "FCA authorisation", lambda: C.check_fca(data, req, firms, discipline)),
    ]
    try:
        if skip_media:
            # Not "not applicable": news applies, it just wasn't read today. Not critical, so the route is unchanged.
            media = CheckResult(check_id="media", title="Adverse media", status=CheckStatus.COULDNT_CHECK,
                                summary="Not re-read in the daily re-check; run a full pre-screen to read news.",
                                not_checked_reason="news isn't re-read in the daily re-check")
            trade, media_items = None, []
        else:
            media, trade, media_items = await check_media(data, news, services.llm, req.declared_trade,
                                                          services.news.provider_used,
                                                          getattr(services.news, "fallback_reason", ""))
            if getattr(services.news, "fallback_reason", "") and media.status != CheckStatus.COULDNT_CHECK:
                media.summary = f"{media.summary} (Read from the free fallback: {services.news.fallback_reason})"
    except Exception as exc:  # noqa: BLE001
        media = CheckResult(check_id="media", title="Adverse media", status=CheckStatus.COULDNT_CHECK,
                            summary=f"Couldn't check: internal error ({type(exc).__name__}).",
                            not_checked_reason=f"internal error ({type(exc).__name__})")
        trade, media_items = None, []
    if not skip_media:
        tick("news", "error" if media.status == CheckStatus.COULDNT_CHECK else "done",
             f"{len(media_items)} results" if media_items else "")
    results.append(media)
    if trade:
        results.append(trade)
    results.append(C.check_ccj())

    # News coverage of an FCA action the Register already shows is the same fact (Monzo, live): keep it as a
    # note pointing at the FCA finding instead of a second referral reason.
    if any(f.rule_id == "fca_discipline" for c in results for f in c.findings):
        for c in results:
            for f in c.findings:
                if f.rule_id == "media_high" and set(f.detail.get("categories", [])) <= {"regulatory_action"}:
                    f.route = Route.NOTE
                    f.text = "News coverage of the FCA action above: " + f.text
            if c.check_id == "media" and c.status == CheckStatus.FLAG and not any(
                    f.route != Route.NOTE for f in c.findings):
                c.status = CheckStatus.INFO
                c.summary = " ".join(f.text for f in c.findings)

    for c in results:
        c.group = casefile.group_of(c.check_id)
        for f in c.findings:
            f.tags = casefile.fraud_tags(f.rule_id)
    route, reasons = decide(results)
    casefile.enrich_findings(results, req.cover_lines)
    card = company_card(data)
    unchecked = [c.title for c in results if c.status == CheckStatus.COULDNT_CHECK]
    sanctions_note = sanctions_list.fetched_at if isinstance(sanctions_list, SanctionsList) else ""
    report = PreScreenReport(
        report_id=uuid.uuid4().hex[:16],
        rules_version=RULES_VERSION,
        company_number=number,
        company_name=data.name,
        request=req,
        route=route,
        route_reasons=reasons,
        checks=results,
        couldnt_check=unchecked,
        media=media_items,
        summary=rules_summary(route, reasons, results),
        llm_calls=services.llm.calls,
        llm_usage=dict(getattr(services.llm, "usage", {}) or {}),
        elapsed_seconds=round(time.perf_counter() - t0, 2),
        company=card,
        broker_questions=casefile.broker_questions(results, llp=profile.get("type") == "llp"),
        proposal_vs_record=casefile.proposal_vs_record(req, card, results, today),
        coverage=casefile.coverage(results),
        timeline=_timeline(data),
        sources_read=sources_read(read_at, sanctions_note),
        proposal_read=proposal_read(req),
        rerun_of=req.rerun_of,
    )
    # Built from the finished report, so the page, the file note and the API use one text.
    payload = report.model_dump(mode="json")
    report.news_view = caseview.news_view(payload)
    report.broker_email = caseview.broker_email(payload)
    return report


# What the underwriter sees for each source: the register's name, never a provider or model name.
SOURCE_LABELS = {
    "companies_house": "Companies House",
    "directors": "Companies House appointments",
    "gazette": "The Gazette",
    "sanctions": "UK Sanctions List",
    "fca": "FCA Register",
    "news": "web news search",
}


def sources_read(read_at: dict[str, dict[str, str]], sanctions_note: str = "") -> list[dict[str, str]]:
    out = []
    for sid, label in SOURCE_LABELS.items():
        r = read_at.get(sid)
        if not r:
            continue
        row = {"id": sid, "label": label, "state": r["state"], "read_at": r["at"],
               "detail": r.get("detail", "") if sid != "news" else ""}
        if sid == "sanctions" and sanctions_note:
            row["list_date"] = sanctions_note
        out.append(row)
    return out


def proposal_read(req: PreScreenRequest) -> dict[str, str]:
    """How the proposal fields were read, in the words the page shows ("Read as £2,400,000")."""
    out: dict[str, str] = {}
    if req.declared_turnover_gbp is not None:
        out["turnover"] = caseview.money_words(req.declared_turnover_gbp)
    if req.declared_years_trading is not None:
        out["years_trading"] = f"{req.declared_years_trading} year{'s' if req.declared_years_trading != 1 else ''}"
    if req.declared_trade:
        out["trade"] = req.declared_trade
    if req.declared_directors:
        out["directors"] = req.declared_directors
    if req.expects_fca_authorisation:
        out["fca"] = "Should be FCA authorised"
    out["given"] = "yes" if out else "no"
    return out


def _timeline(data: C.CompanyData) -> list[dict[str, Any]]:
    """Cosmetic: a bug here must never fail the report."""
    try:
        return casefile.timeline(data)
    except Exception:  # noqa: BLE001
        return []


async def _gazette_missing() -> SourceError:
    return SourceError("The Gazette", "no_key", "not connected")
