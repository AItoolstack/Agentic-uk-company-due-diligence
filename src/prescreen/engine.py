"""
Runs one pre-screen: fetch everything for one company number, run the checks,
apply the rules, write the report.

Order matters only in two places: identity comes first (nothing runs without a
number), and director lookups need the officer list. Everything else is
fetched in parallel. A typical case is 7 + 2 x directors Companies House calls,
2 search calls and one model call.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable

from src.prescreen import checks as C
from src.prescreen.media import check_media
from src.prescreen.models import CheckResult, CheckStatus, PreScreenReport, PreScreenRequest, Route
from src.prescreen.rules import RULES_VERSION, THRESHOLDS, decide
from src.prescreen.sources.companies_house import CompaniesHouse
from src.prescreen.sources.fca import FCARegister
from src.prescreen.sources.http import NOT_FOUND, SourceError
from src.prescreen.sources.news import NewsSearch
from src.prescreen.sources.sanctions import SanctionsList
from src.prescreen.llm import JSONChat


@dataclass
class Services:
    ch: CompaniesHouse
    fca: FCARegister
    news: NewsSearch
    llm: JSONChat
    sanctions: Any  # async callable returning SanctionsList, or a SanctionsList / SourceError
    today: date | None = None


async def _safe(coro: Awaitable[Any]) -> Any:
    try:
        return await coro
    except SourceError as err:
        return err


async def _sanctions(services: Services) -> SanctionsList | SourceError:
    s = services.sanctions
    if isinstance(s, (SanctionsList, SourceError)):
        return s
    try:
        return await s()
    except SourceError as err:
        return err


ROUTE_WORDS = {
    Route.NOTE: "Note only",
    Route.REFER: "Refer",
    Route.OUTSIDE_APPETITE: "Outside appetite",
}


def rules_summary(name: str, route: Route, reasons: list[str], checks: list[CheckResult]) -> str:
    ran = [c for c in checks if c.status != CheckStatus.NOT_APPLICABLE]
    clear = sum(1 for c in ran if c.status == CheckStatus.CLEAR)
    flagged = sum(1 for c in ran if c.status == CheckStatus.FLAG)
    unchecked = [c.title for c in ran if c.status == CheckStatus.COULDNT_CHECK]
    parts = [f"{ROUTE_WORDS[route]}. {len(ran)} checks: {clear} clear, {flagged} flagged, "
             f"{len(unchecked)} couldn't be checked."]
    if reasons:
        parts.append("Main reason: " + reasons[0])
    if unchecked:
        parts.append("Not checked: " + ", ".join(unchecked) + ".")
    parts.append("This is a recommendation. The underwriter decides.")
    return " ".join(parts)


async def run_prescreen(req: PreScreenRequest, services: Services) -> PreScreenReport:
    t0 = time.perf_counter()
    today = services.today or date.today()
    ch = services.ch
    number = req.company_number.strip().upper()

    profile = await _safe(ch.profile(number))
    if isinstance(profile, SourceError) or profile is NOT_FOUND:
        raise ValueError(
            profile.plain if isinstance(profile, SourceError) else f"No company with number {number}."
        )

    links = profile.get("links") or {}
    officers, pscs, statements, charges, insolvency, sanctions_list = await asyncio.gather(
        _safe(ch.officers(number)),
        _safe(ch.pscs(number)),
        _safe(ch.psc_statements(number)) if "persons_with_significant_control_statements" in links
        else _none(),
        _safe(ch.charges(number)) if ("charges" in links or profile.get("has_charges")) else _none(),
        _safe(ch.insolvency(number)) if ("insolvency" in links or profile.get("has_insolvency_history"))
        else _none(),
        _sanctions(services),
    )
    data = C.CompanyData(number=number, profile=profile, today=today, officers=officers, pscs=pscs,
                         psc_statements=statements, charges=charges, insolvency=insolvency)

    directors = C.active_officers(data)[: THRESHOLDS["max_directors_checked"]]
    ids = [(d, C.officer_id(d)) for d in directors]
    appts = await asyncio.gather(*[_safe(ch.appointments(oid)) for _, oid in ids if oid])
    data.appointments = {oid: r for (_, oid), r in zip([x for x in ids if x[1]], appts)}
    disq = await asyncio.gather(*[_safe(ch.search_disqualified(d.get("name", ""))) for d in directors])
    data.disqualified = {d.get("name", ""): r for d, r in zip(directors, disq)}

    # Detail pages only for name+DOB candidates, to read the disqualification period.
    detail_links = []
    for d in directors:
        res = data.disqualified.get(d.get("name", ""))
        if isinstance(res, dict):
            for h in res.get("items") or []:
                if C._name_tokens(h.get("title", "")) == C._name_tokens(d.get("name", "")):
                    link = (h.get("links") or {}).get("self")
                    if link:
                        detail_links.append(link)
    if detail_links:
        details = await asyncio.gather(*[_safe(ch.disqualified_natural(l)) for l in detail_links])
        data.disq_details = dict(zip(detail_links, details))

    # FCA only when relevant; news in parallel with it.
    fca_needed = C.fca_applicable(data, req.expects_fca_authorisation)

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

    (firms, discipline), news = await asyncio.gather(fca_lookup(), _safe(services.news.search(data.name)))

    results: list[CheckResult] = [
        C.check_status(data),
        C.check_age_and_names(data),
        C.check_filings(data),
        C.check_insolvency(data),
        C.check_charges(data),
        C.check_directors_disqualified(data),
        C.check_directors_insolvency(data),
        C.check_ownership(data),
        C.check_sanctions(data, sanctions_list),
        C.check_fca(data, req.expects_fca_authorisation, firms, discipline),
    ]
    media, trade, media_items = await check_media(data, news, services.llm, req.declared_trade,
                                                  services.news.provider_used)
    results.append(media)
    if trade:
        results.append(trade)
    results.append(C.check_ccj())

    route, reasons = decide(results)
    unchecked = [c.title for c in results if c.status == CheckStatus.COULDNT_CHECK]
    return PreScreenReport(
        report_id=uuid.uuid4().hex[:12],
        rules_version=RULES_VERSION,
        company_number=number,
        company_name=data.name,
        request=req,
        route=route,
        route_reasons=reasons,
        checks=results,
        couldnt_check=unchecked,
        media=media_items,
        summary=rules_summary(data.name, route, reasons, results),
        llm_calls=services.llm.calls,
        elapsed_seconds=round(time.perf_counter() - t0, 2),
    )


async def _none() -> None:
    return None
