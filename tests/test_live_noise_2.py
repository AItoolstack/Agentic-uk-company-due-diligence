"""
Noise from the rest of the second live batch (BrewDog, Tesco, Revolut, Greggs; 19 Oct 2026).
"""

from __future__ import annotations

import json

from src.prescreen import casefile, demo_cases
from src.prescreen.identity import normalise_name
from src.prescreen.models import Route
from src.prescreen.rules import decide
from src.prescreen.sources.fca import FCARegister
from src.prescreen.sources.news import NewsSearch
from tests.test_live_noise import _c, _f
from tests.test_prescreen import _check, _run, _services

MONZO = "09446231"


def _answer(rows: dict[int, dict]) -> str:
    return json.dumps({"items": [{"i": i, "about_this_company": "yes", **r} for i, r in rows.items()],
                       "trade_consistency": {"verdict": "not_asked", "reason": ""}})


def _with_news(news: list[dict], answer: str):
    s = _services(MONZO, llm_answers=[answer])
    s.news = NewsSearch(None, fixtures=news)
    return s


def test_gazette_notices_for_an_insolvency_on_record_are_not_extra_reasons_or_questions():
    checks = [_c("status", _f("company_insolvent", Route.OUTSIDE_APPETITE, "In administration.")),
              _c("insolvency", _f("insolvency_history", Route.REFER, "Insolvency case on record"),
                 _f("gazette_insolvency_notice", Route.REFER, "Gazette: Administrators appointed, 06 Mar 2026."))]
    route, reasons = decide(checks)
    assert route == Route.OUTSIDE_APPETITE
    assert not [r for r in reasons if r.startswith("Gazette")]
    only = [_c("insolvency", _f("gazette_insolvency_notice", Route.REFER, "Gazette: Petition to wind up"))]
    assert decide(only)[1] == ["Gazette: Petition to wind up"]
    qs = casefile.broker_questions([_c("insolvency", _f("insolvency_history", Route.REFER),
                                       _f("gazette_insolvency_notice", Route.REFER))])
    assert [q["rule_id"] for q in qs] == ["insolvency_history"]


def test_serious_news_older_than_the_lookback_is_a_note():
    news = [demo_cases._news("Monzo fined for false accounting", "2017-03-28", "bbc.co.uk", "old story")]
    report = _run(MONZO, services=_with_news(news, _answer(
        {0: {"category": "fraud_or_financial_crime", "severity": "high", "reason": "fine"}})))
    rules = {f.rule_id: f for c in report.checks for f in c.findings}
    assert "media_high" not in rules and rules["media_old"].route == Route.NOTE
    assert "older than 5 years" in rules["media_old"].text
    assert not [r for r in report.route_reasons if "false accounting" in r]


def test_media_summary_never_says_nothing_adverse_above_an_adverse_finding():
    news = [demo_cases._news("Monzo to cut 700 jobs", "2026-09-30", "bbc.co.uk", "jobs")]
    report = _run(MONZO, services=_with_news(news, _answer(
        {0: {"category": "operational_incident", "severity": "medium", "reason": "job cuts"}})))
    media = _check(report, "media")
    assert media.status.value == "info"
    assert "nothing adverse" not in media.summary and "700 jobs" in media.summary


def test_fca_search_retries_with_the_bare_name_and_lists_what_it_saw():
    case = demo_cases.cases()[MONZO]
    firm = case["fca"]["/search?q=monzo bank limited"]
    bare = normalise_name("MONZO BANK LIMITED").title()
    s = _services(MONZO)
    s.fca = FCARegister(None, "", "", "", fixtures={f"/search?q={bare}".lower(): firm,
                                                     "/Firm/730427/DisciplinaryHistory": {"Data": []}})
    report = _run(MONZO, services=s, cover_lines=["management_liability"])
    assert "fca_authorised" in {f.rule_id for f in _check(report, "fca").findings}

    s.fca = FCARegister(None, "", "", "", fixtures={"/search?q=monzo bank limited": {"Data": [
        {"Name": "Monzo Travel Ltd", "Reference Number": "1", "Status": "Authorised"}]}})
    nf = next(f for f in _check(_run(MONZO, services=s, cover_lines=["management_liability"]), "fca").findings
              if f.rule_id == "fca_not_found")
    assert nf.detail["register_names"] == ["Monzo Travel Ltd (FRN 1, Authorised)"]


def test_fca_search_follows_the_registers_next_page_until_the_name_matches():
    import asyncio

    import httpx
    base = "https://register.fca.org.uk/services/V0.1"
    pages = {
        1: {"Data": [{"Name": "Revolut Trading Ltd", "Reference Number": "832790", "Status": "No longer authorised"}],
            "ResultInfo": {"page": "1", "Next": f"{base}/Search?q=REVOLUT+LTD&type=firm&pgnp=2"}},
        2: {"Data": [{"Name": "Revolut Ltd", "Reference Number": "900562", "Status": "Authorised"}],
            "ResultInfo": {"page": "2", "Next": f"{base}/Search?q=REVOLUT+LTD&type=firm&pgnp=3"}},
        3: {"Data": [{"Name": "never read", "Reference Number": "3", "Status": ""}], "ResultInfo": {}},
    }
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("pgnp", "1"))
        seen.append(page)
        return httpx.Response(200, json=pages[page])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fca = FCARegister(client, "key", "a@b.c", base)
            target = normalise_name("REVOLUT LTD")
            return fca, await fca.search_firms("REVOLUT LTD", want=lambda n: normalise_name(n) == target)

    fca, firms = asyncio.run(go())
    assert [f["frn"] for f in firms] == ["832790", "900562"]
    assert seen == [1, 2] and fca.last_result_info["pages_read"] == 2


def test_fca_search_never_follows_a_link_off_the_register():
    assert FCARegister._next_url({"Next": "https://evil.example/Search?q=x"}) == ""
    assert FCARegister._next_url({"Next": "https://register.fca.org.uk/services/V0.1/Search?q=x&pgnp=2"})
