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

from src.prescreen import checks as C
from src.prescreen.llm import JSONChat
from src.prescreen.media import check_media
from src.prescreen.models import CheckResult, CheckStatus, PreScreenReport, PreScreenRequest, Route
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
    ran = [c for c in checks if c.status != CheckStatus.NOT_APPLICABLE]
    clear = sum(1 for c in ran if c.status in (CheckStatus.CLEAR, CheckStatus.INFO))
    flagged = sum(1 for c in ran if c.status == CheckStatus.FLAG)
    unchecked = [c.title for c in ran if c.status == CheckStatus.COULDNT_CHECK]
    parts = [f"{ROUTE_WORDS[route]}. {len(ran)} checks: {clear} without flags, {flagged} flagged, "
             f"{len(unchecked)} couldn't be checked."]
    if reasons:
        parts.append("Main reason: " + reasons[0])
    if unchecked:
        parts.append("Not checked: " + ", ".join(unchecked) + ".")
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
    }


async def run_prescreen(req: PreScreenRequest, services: Services, progress: Progress | None = None) -> PreScreenReport:
    def tick(stage: str, state: str, detail: str = "") -> None:
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
    data.disq_details = dict(zip(detail_links, details))
    data.parent_pscs = {p: r for p, r in zip(parents, parent_pscs) if isinstance(r, dict)}
    tick("directors", "done", f"{len(people)} checked")

    # FCA only when relevant; news in parallel with it.
    fca_needed = C.fca_applicable(data, req)

    async def fca_lookup() -> tuple[Any, Any]:
        if not fca_needed:
            return None, None
        firms = await _safe(services.fca.search_firms(data.name))
        discipline = None
        if isinstance(firms, list):
            exact = [f for f in firms if C.normalise_name(f["name"]) == C.normalise_name(data.name)]
            if exact:
                discipline = await _safe(services.fca.disciplinary_history(exact[0]["frn"]))
        return firms, discipline

    prev_names = [p.get("name", "") for p in profile.get("previous_company_names") or []]
    tick("fca", "start" if fca_needed else "skip", "" if fca_needed else "not a regulated trade")
    tick("news", "start")
    (firms, discipline), news = await asyncio.gather(
        fca_lookup(), _safe(services.news.search(data.name, previous_names=prev_names)))
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
        media, trade, media_items = await check_media(data, news, services.llm, req.declared_trade,
                                                      services.news.provider_used)
    except Exception as exc:  # noqa: BLE001
        media = CheckResult(check_id="media", title="Adverse media", status=CheckStatus.COULDNT_CHECK,
                            summary=f"Couldn't check: internal error ({type(exc).__name__}).",
                            not_checked_reason=f"internal error ({type(exc).__name__})")
        trade, media_items = None, []
    tick("news", "error" if media.status == CheckStatus.COULDNT_CHECK else "done",
         f"{len(media_items)} results" if media_items else "")
    results.append(media)
    if trade:
        results.append(trade)
    results.append(C.check_ccj())

    route, reasons = decide(results)
    unchecked = [c.title for c in results if c.status == CheckStatus.COULDNT_CHECK]
    return PreScreenReport(
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
        elapsed_seconds=round(time.perf_counter() - t0, 2),
        company=company_card(data),
    )


async def _gazette_missing() -> SourceError:
    return SourceError("The Gazette", "no_key", "not connected")
