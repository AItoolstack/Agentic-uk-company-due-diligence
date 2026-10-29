"""
Pre-screen engine tests. Real Companies House responses (Wilko, Monzo, captured
18 Oct 2026) plus invented cases. No network, no real model.

Most tests pin one behaviour the old app got wrong on live data, or a point
from the underwriter and engineering reviews. The invariant under test:
a check is CLEAR only when it actually looked at everything.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from src.prescreen import demo_cases
from src.prescreen.engine import CompanyNotFound, NotCovered, Services, run_prescreen
from src.prescreen.identity import normalise_name, normalise_number, resolve
from src.prescreen.llm import JSONChat, LLMError
from src.prescreen.models import CheckStatus, PreScreenRequest, Route
from src.prescreen.service import DemoTransport
from src.prescreen.sources.companies_house import CompaniesHouse, FixtureTransport
from src.prescreen.sources.fca import FCARegister
from src.prescreen.sources.gazette import Gazette
from src.prescreen.sources.http import NOT_FOUND, SourceError, get_json
from src.prescreen.sources.news import NewsSearch, short_name
from src.prescreen.sources.sanctions import Screened, parse_csv, screen


def _all_ch() -> dict:
    data = {}
    for c in demo_cases.cases().values():
        data.update({k.lower() if "?q=" in k else k: v for k, v in c["ch"].items()})
    return data


def _services(number: str, llm_answers: list[str] | None = None, sanctions=None) -> Services:
    case = demo_cases.cases().get(number, {})
    return Services(
        ch=CompaniesHouse(DemoTransport(_all_ch())),
        fca=FCARegister(None, "", "", "", fixtures={k.lower() if "?q=" in k else k: v
                                                     for k, v in (case.get("fca") or {}).items()}),
        news=NewsSearch(None, fixtures=case.get("news", [])),
        gazette=Gazette(None, fixtures=case.get("gazette", [])),
        llm=JSONChat(None, "groq", "test", "", scripted=list(llm_answers or [])),
        sanctions=sanctions if sanctions is not None else parse_csv(demo_cases.FICTIONAL_SANCTIONS_CSV, "test", "test"),
        today=demo_cases.TODAY,
    )


def _all_reviewed(n: int, overrides: dict[int, dict] | None = None) -> str:
    rows = []
    for i in range(n):
        row = {"i": i, "about_this_company": "yes", "category": "neutral", "severity": "low", "reason": "routine"}
        row.update((overrides or {}).get(i, {}))
        rows.append(row)
    return json.dumps({"items": rows, "trade_consistency": {"verdict": "not_asked", "reason": ""}})


def _run(number: str, services: Services | None = None, **kw):
    req = PreScreenRequest(company_number=number, declared_trade=kw.pop("declared_trade", ""),
                           expects_fca_authorisation=kw.pop("expects_fca", False),
                           cover_lines=kw.pop("cover_lines", []),
                           declared_turnover_gbp=kw.pop("turnover", None))
    return asyncio.run(run_prescreen(req, services or _services(number, **kw)))


def _check(report, check_id):
    return next(c for c in report.checks if c.check_id == check_id)


def _rules(report):
    return {f.rule_id for c in report.checks for f in c.findings}


# ---------------------------------------------------------------- identity

def test_number_normalisation():
    assert normalise_number("9563205") == "09563205"
    assert normalise_number(" sc 123456 ") == "SC123456"
    assert normalise_number("Wilko Limited") is None


def test_request_rejects_path_like_numbers():
    with pytest.raises(ValueError):
        PreScreenRequest(company_number="../search")
    assert PreScreenRequest(company_number="9563205").company_number == "09563205"


def test_unknown_cover_lines_are_dropped():
    assert PreScreenRequest(company_number="09563205", cover_lines=["property", "hack"]).cover_lines == ["property"]


def test_name_normalisation_treats_ltd_and_limited_alike():
    assert normalise_name("The Wilko Limited") == normalise_name("WILKO LTD")


def test_name_search_with_no_exact_match_does_not_resolve():
    ch = CompaniesHouse(FixtureTransport({"/search/companies?q=apex building services": {"items": [
        {"company_number": "11111111", "title": "APEX BUILDING SERVICES (UK) LTD", "company_status": "active"},
        {"company_number": "22222222", "title": "APEX BUILDING SERVICE LIMITED", "company_status": "dissolved"},
    ]}}))
    result = asyncio.run(resolve(ch, "Apex Building Services"))
    assert not result.resolved and len(result.candidates) == 2 and "Pick" in result.message


def test_name_search_with_two_exact_matches_asks_the_user():
    ch = CompaniesHouse(FixtureTransport({"/search/companies?q=wilko limited": {"items": [
        {"company_number": "09563205", "title": "WILKO LIMITED", "company_status": "active"},
        {"company_number": "00000001", "title": "WILKO LIMITED", "company_status": "dissolved"},
    ]}}))
    result = asyncio.run(resolve(ch, "Wilko Limited"))
    assert not result.resolved and "2 companies" in result.message


def test_unique_exact_active_name_resolves():
    ch = CompaniesHouse(FixtureTransport({"/search/companies?q=monzo bank ltd": {"items": [
        {"company_number": "09446231", "title": "MONZO BANK LIMITED", "company_status": "active"},
        {"company_number": "33333333", "title": "MONZO BANK HOLDING GROUP LIMITED", "company_status": "active"},
    ]}}))
    result = asyncio.run(resolve(ch, "Monzo Bank Ltd"))
    assert result.resolved and result.company_number == "09446231"


def test_search_failure_is_reported_not_swallowed():
    ch = CompaniesHouse(FixtureTransport({"/search/companies?q=thames water": SourceError(
        "Companies House", "rate_limited", "HTTP 429")}))
    result = asyncio.run(resolve(ch, "Thames Water"))
    assert not result.resolved and "rate limit" in result.message


def test_unknown_company_number_raises():
    with pytest.raises(CompanyNotFound):
        _run("ZZ999999")


# ---------------------------------------------------------------- Wilko (real profile)

def test_wilko_news_before_the_rename_is_excluded_without_the_model():
    report = _run("09563205", llm_answers=[_all_reviewed(4)])
    excluded = [m for m in report.media if m.about_this_company == "no"]
    assert len(excluded) == 3, "the three Aug-Sep 2023 articles predate the Nov 2023 rename"
    assert all("before this company took its current name" in m.reason for m in excluded)
    assert _check(report, "media").status == CheckStatus.CLEAR


def test_wilko_rename_dormant_and_dissolved_subsidiaries_are_info_not_flags():
    report = _run("09563205", llm_answers=[_all_reviewed(4)])
    assert {"renamed_recently", "dormant", "director_dissolved_context"} <= _rules(report)
    assert "director_insolvent_links" not in _rules(report)
    assert _check(report, "filings").status == CheckStatus.INFO
    assert report.route == Route.NOTE


def test_dormant_company_with_a_declared_trade_refers():
    report = _run("09563205", llm_answers=[_all_reviewed(4)], declared_trade="Homeware retail")
    assert "dormant_but_trading" in _rules(report)
    assert report.route == Route.REFER


def test_wilko_uses_one_model_call():
    assert _run("09563205", llm_answers=[_all_reviewed(4)]).llm_calls == 1


# ---------------------------------------------------------------- Monzo (real profile)

MONZO_LLM = _all_reviewed(2, {0: {"category": "regulatory_action", "severity": "high", "reason": "FCA fine"}})


def test_monzo_fca_discipline_refers_for_financial_lines():
    report = _run("09446231", llm_answers=[MONZO_LLM], cover_lines=["management_liability"])
    assert {"fca_authorised", "fca_discipline"} <= _rules(report)
    assert next(f for c in report.checks for f in c.findings if f.rule_id == "fca_discipline").route == Route.REFER
    assert report.route == Route.REFER


def test_monzo_fca_discipline_is_a_note_for_property_only():
    report = _run("09446231", llm_answers=[MONZO_LLM], cover_lines=["property"])
    assert next(f for c in report.checks for f in c.findings if f.rule_id == "fca_discipline").route == Route.NOTE


def test_fca_not_applicable_for_unregulated_trade():
    assert _check(_run("ZZ000002"), "fca").status == CheckStatus.NOT_APPLICABLE


def test_fca_runs_when_user_says_firm_should_be_authorised():
    fca = _check(_run("ZZ000002", expects_fca=True), "fca")
    assert fca.status != CheckStatus.NOT_APPLICABLE
    assert any(f.route == Route.REFER for f in fca.findings)


# ---------------------------------------------------------------- invented cases

def test_phoenix_pattern_is_outside_appetite():
    report = _run("ZZ000001")
    assert report.route == Route.OUTSIDE_APPETITE
    assert {"phoenix_pattern", "director_insolvent_links", "company_new"} <= _rules(report)


def test_liquidated_then_dissolved_company_still_counts():
    report = _run("ZZ000001")
    links = next(f for c in report.checks for f in c.findings if f.rule_id == "director_insolvent_links")
    assert links.detail["count"] == 3
    assert "EXAMPLE EVENTS LTD" in links.text and "EXAMPLE PROPERTY LTD" not in links.text


def test_namesake_on_sanctions_list_with_different_dob_is_discounted():
    sanc = _check(_run("ZZ000001"), "sanctions")
    assert "sanctions_discounted" in {f.rule_id for f in sanc.findings}
    assert sanc.status == CheckStatus.INFO


def test_distress_case_routes():
    report = _run("ZZ000002")
    assert report.route == Route.OUTSIDE_APPETITE
    assert {"strike_off_compulsory", "accounts_overdue_serious", "confirmation_overdue",
            "charges_burst", "winding_up_petition"} <= _rules(report)
    assert "accounts_overdue" not in _rules(report), "one overdue finding, not two"


def test_compulsory_strike_off_alone_is_refer_not_outside():
    data = _all_ch()
    data["/company/ZZ000002/charges"] = {"items": []}
    services = _services("ZZ000002")
    services.ch = CompaniesHouse(DemoTransport(data))
    services.gazette = Gazette(None, fixtures=[])
    report = _run("ZZ000002", services=services)
    assert "strike_off_compulsory" in _rules(report)
    f = next(f for c in report.checks for f in c.findings if f.rule_id == "strike_off_compulsory")
    assert f.route == Route.REFER


def test_sanctions_name_and_dob_match_is_compliance_hold():
    report = _run("ZZ000004")
    assert report.route == Route.COMPLIANCE_HOLD
    assert {"sanctions_strong", "psc_secrecy_jurisdiction"} <= _rules(report)


def test_llp_members_are_checked_and_screened():
    report = _run("OC000005", cover_lines=["professional_indemnity"])
    disq = _check(report, "directors_disqualified")
    assert disq.status == CheckStatus.CLEAR and "3 member(s)" in disq.summary
    assert "officers_left" in _rules(report)
    assert report.route == Route.NOTE


# ---------------------------------------------------------------- couldn't check

def test_sources_down_means_refer_and_every_gap_is_named():
    report = _run("ZZ000003")
    assert report.route == Route.REFER
    for title in ("Directors: disqualification", "Directors: other companies",
                  "Sanctions (UK Sanctions List)", "Ownership (PSC register)", "Adverse media"):
        assert title in report.couldnt_check
    assert any("couldn't be checked" in r for r in report.route_reasons)


def test_missing_sanctions_list_is_never_clear():
    report = _run("09563205", llm_answers=[_all_reviewed(4)],
                  sanctions=SourceError("UK Sanctions List", "unavailable", "HTTP 503"))
    assert _check(report, "sanctions").status == CheckStatus.COULDNT_CHECK
    assert report.route == Route.REFER


def test_gazette_down_makes_insolvency_couldnt_check():
    services = _services("09563205", llm_answers=[_all_reviewed(4)])
    services.gazette = Gazette(None, fixtures=SourceError("The Gazette", "unavailable", "HTTP 503"))
    report = _run("09563205", services=services)
    assert _check(report, "insolvency").status == CheckStatus.COULDNT_CHECK
    assert report.route == Route.REFER


def test_model_skipping_results_is_couldnt_check_not_clear():
    report = _run("09446231", llm_answers=[json.dumps({"items": []})])
    media = _check(report, "media")
    assert media.status == CheckStatus.COULDNT_CHECK
    assert "weren't reviewed" in media.not_checked_reason


def test_model_failure_makes_media_couldnt_check():
    media = _check(_run("09446231", llm_answers=["not json", "still not json"]), "media")
    assert media.status == CheckStatus.COULDNT_CHECK and "review failed" in media.not_checked_reason


def test_no_model_configured_is_couldnt_check_and_trade_check_is_shown():
    services = _services("09446231")
    services.llm = JSONChat(None, "groq", "m", "")
    report = _run("09446231", services=services, declared_trade="Bank")
    assert _check(report, "media").status == CheckStatus.COULDNT_CHECK
    assert _check(report, "trade").status == CheckStatus.COULDNT_CHECK


def test_charges_404_when_profile_links_them_is_couldnt_check():
    data = _all_ch()
    del data["/company/09446231/charges"]
    services = _services("09446231", llm_answers=[MONZO_LLM])
    services.ch = CompaniesHouse(DemoTransport(data))
    assert _check(_run("09446231", services=services), "charges").status == CheckStatus.COULDNT_CHECK


def test_more_directors_than_the_cap_is_couldnt_check():
    data = _all_ch()
    data["/company/ZZ000002/officers"] = {"items": [
        demo_cases._officer(f"PERSON{i}, Alex", f"p{i}", (1, 1970), "2015-01-01") for i in range(17)]}
    services = _services("ZZ000002")
    services.ch = CompaniesHouse(DemoTransport(data))
    report = _run("ZZ000002", services=services)
    assert _check(report, "directors_disqualified").status == CheckStatus.COULDNT_CHECK
    assert "limit" in _check(report, "directors_disqualified").not_checked_reason


def test_common_name_with_more_hits_than_read_is_couldnt_check():
    data = _all_ch()
    data["/search/disqualified-officers?q=sample, morgan"] = {"items": [], "total_results": 250}
    services = _services("ZZ000002")
    services.ch = CompaniesHouse(DemoTransport(data))
    assert _check(_run("ZZ000002", services=services), "directors_disqualified").status == CheckStatus.COULDNT_CHECK


def test_ccj_always_shown_as_not_checked():
    assert _check(_run("ZZ000002"), "ccj").status == CheckStatus.COULDNT_CHECK


def test_a_bug_in_one_check_does_not_lose_the_report(monkeypatch):
    from src.prescreen import checks
    monkeypatch.setattr(checks, "check_charges", lambda data: 1 / 0)
    report = _run("ZZ000002")
    assert _check(report, "charges").status == CheckStatus.COULDNT_CHECK


# ---------------------------------------------------------------- disqualification lookup

def _with_disq(hit: dict, detail: dict | None = None):
    data = _all_ch()
    data["/search/disqualified-officers?q=sample, morgan"] = {"items": [hit]}
    if detail:
        data[hit["links"]["self"]] = detail
    services = _services("ZZ000002")
    services.ch = CompaniesHouse(DemoTransport(data))
    return _run("ZZ000002", services=services)


def test_disqualified_director_with_matching_dob_is_outside_appetite():
    report = _with_disq({"title": "Morgan SAMPLE", "date_of_birth": "1971-08-02",
                         "links": {"self": "/disqualified-officers/natural/abc123"}},
                        {"disqualifications": [{"disqualified_until": "2030-01-01"}]})
    assert "director_disqualified" in _rules(report)


def test_disqualified_match_with_extra_middle_name_is_still_found():
    report = _with_disq({"title": "Morgan Lee SAMPLE", "date_of_birth": "1971-08-02",
                         "links": {"self": "/disqualified-officers/natural/abc124"}},
                        {"disqualifications": [{"disqualified_until": "2031-01-01"}]})
    assert "director_disqualified" in _rules(report)


def test_disqualified_namesake_with_other_dob_is_not_flagged():
    report = _with_disq({"title": "Morgan SAMPLE", "date_of_birth": "1950-01-01",
                         "links": {"self": "/disqualified-officers/natural/zzz"}})
    assert "director_disqualified" not in _rules(report) and "disq_name_only" in _rules(report)


# ---------------------------------------------------------------- sanctions

def test_sanctions_csv_without_expected_columns_is_rejected():
    with pytest.raises(SourceError):
        parse_csv("a,b,c\n1,2,3\n")


def test_sanctions_csv_with_wrong_type_column_is_rejected():
    bad = demo_cases.FICTIONAL_SANCTIONS_CSV.replace('"Individual, Entity, Ship"', "Group Type")
    bad = bad.replace(",Individual,", ",Person,").replace(",Entity,", ",Org,")
    with pytest.raises(SourceError):
        parse_csv(bad)


def test_sanctions_csv_parses_header_after_metadata_line():
    sl = parse_csv(demo_cases.FICTIONAL_SANCTIONS_CSV)
    assert sl.size == 3 and any(e.kind == "individual" for e in sl.entries)


def test_unparseable_list_dob_is_possible_not_cleared():
    csv_text = demo_cases.FICTIONAL_SANCTIONS_CSV.replace("12/04/1966", "c. 1966")
    hits = screen([Screened("Viktor Ivanovich FICTIONOV", "director", True, 1980, 1)], parse_csv(csv_text))
    assert hits and hits[0].strength == "possible"


def test_corporate_director_is_screened_as_an_entity():
    hits = screen([Screened("EXAMPLE SHIPPING CO LTD", "corporate director", False)],
                  parse_csv(demo_cases.FICTIONAL_SANCTIONS_CSV))
    assert hits and hits[0].entry.unique_id == "FIC0002"


# ---------------------------------------------------------------- sources and plumbing

def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("status,kind", [(401, "no_key"), (403, "no_key"), (429, "rate_limited"),
                                         (503, "unavailable"), (418, "bad_response")])
def test_http_errors_map_to_couldnt_check_kinds(status, kind, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", lambda *_: _noop())

    async def go():
        async with _client(lambda req: httpx.Response(status, headers={"retry-after": "0"})) as c:
            return await get_json(c, "X", "https://example.test/x", retries=1)
    with pytest.raises(SourceError) as exc:
        asyncio.run(go())
    assert exc.value.kind == kind


async def _noop():
    return None


def test_http_404_is_not_found_not_error():
    async def go():
        async with _client(lambda req: httpx.Response(404)) as c:
            return await get_json(c, "X", "https://example.test/x")
    assert asyncio.run(go()) is NOT_FOUND


def test_brave_failure_falls_back_to_gdelt_and_says_so():
    def handler(req: httpx.Request) -> httpx.Response:
        if "brave" in req.url.host:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"articles": [{"title": "Thing", "url": "https://n.test/a",
                                                       "seendate": "20261001T120000Z", "domain": "n.test"}]})

    async def go():
        async with _client(handler) as c:
            news = NewsSearch(c, brave_key="k")
            items = await news.search("EXAMPLE LTD")
            return news, items
    news, items = asyncio.run(go())
    assert "fallback" in news.provider_used and items[0]["url"] == "https://n.test/a"


def test_groq_json_rejection_gets_one_repair_attempt():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(400, json={"error": {"code": "json_validate_failed"}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                                                      "message": {"content": '{"items": []}'}}]})

    async def go():
        async with _client(handler) as c:
            return await JSONChat(c, "groq", "openai/gpt-oss-20b", "k").complete_json("s", "u")
    assert asyncio.run(go()) == {"items": []} and calls["n"] == 2


def test_truncated_model_answer_is_an_error_not_partial_json():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": '{"it'}}]})

    async def go():
        async with _client(handler) as c:
            return await JSONChat(c, "groq", "openai/gpt-oss-20b", "k").complete_json("s", "u")
    with pytest.raises(LLMError):
        asyncio.run(go())


def test_gazette_parses_live_shaped_response():
    payload = {"entry": [{"f:notice-code": "2450", "title": "Petitions to Wind Up (Companies)",
                          "published": "2026-09-01T00:00:00", "content": "<div><p>Company Number: …</p></div>",
                          "link": [{"@href": "https://www.thegazette.co.uk/id/notice/1", "@rel": "self"},
                                   {"@href": "https://www.thegazette.co.uk/notice/1"}]}]}

    async def go():
        async with _client(lambda req: httpx.Response(200, json=payload)) as c:
            return await Gazette(c).insolvency_notices("12345678", "X LTD")
    out = asyncio.run(go())
    assert out[0]["code"] == "2450" and out[0]["url"] == "https://www.thegazette.co.uk/notice/1"


def test_short_name_for_search():
    assert short_name("WILKO LIMITED") == "Wilko"
    assert short_name("Monzo Bank Ltd") == "Monzo Bank"


# ---------------------------------------------------------------- API (demo mode)

def test_stream_endpoint_sends_progress_then_report(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.api.app import app
    from src.prescreen import service
    from src.prescreen.store import FileStore, set_store
    set_store(FileStore(tmp_path))
    monkeypatch.setattr(service.settings, "use_mock_data", True)
    with TestClient(app) as client:
        r = client.post("/api/prescreen/stream", json={"company_number": "ZZ000001"})
        events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
        assert events[0]["type"] == "stages"
        assert any(e["type"] == "progress" and e["stage"] == "directors" and e["state"] == "done" for e in events)
        report = events[-1]["report"]
        assert report["route"] == "outside_appetite" and report["company"]["incorporated"]
        recent = client.get("/api/prescreen/recent").json()
        assert recent[0]["report_id"] == report["report_id"]
        bad = client.post("/api/prescreen/stream", json={"company_number": "ZZ999999"})
        assert json.loads(bad.text.splitlines()[-1])["status"] == 404


def test_identity_card_has_sic_labels():
    report = _run("ZZ000001")
    assert report.company["sic"][0] == {"code": "10710", "label": "Manufacture of bread, fresh pastry and cakes"}
    assert report.company["officers"][0]["name"] == "EXAMPLE, Jordan Alex"


# ---------------------------------------------------------------- the live UK Sanctions List layout

REAL_HEADER = ("Last Updated,Unique ID,OFSI Group ID,UN Reference Number,Name 6,Name 1,Name 2,Name 3,Name 4,Name 5,"
               "Name type,Alias strength,Title,Name non-latin script,Non-latin script type,Non-latin script language,"
               "Regime Name,Designation Type,Designation source,Sanctions Imposed,Other Information,"
               "UK Statement of Reasons,Address Line 1,Address Line 2,Address Line 3,Address Line 4,Address Line 5,"
               "Address Line 6,Address Postal Code,Address Country,Phone number,Website,Email address,Date Designated,"
               "D.O.B,Nationality(/ies),National Identifier number,National Identifier additional information,"
               "Passport number,Passport additional information,Position,Gender,Town of birth,Country of birth,"
               "Type of entity,Subsidiaries,Parent company,Business registration number (s),IMO number,"
               "Current owner/operator (s),Previous owner/operator (s),Current believed flag of ship,Previous flags,"
               "Type of ship,Tonnage of ship,Length of ship,Year Built,Hull identification number (HIN)")


def _real_row(uid, name6, name1, kind, dob="", type_of_entity=""):
    cols = REAL_HEADER.split(",")
    row = [""] * len(cols)
    row[cols.index("Unique ID")], row[cols.index("Name 6")], row[cols.index("Name 1")] = uid, name6, name1
    row[cols.index("Designation Type")], row[cols.index("D.O.B")] = kind, dob
    row[cols.index("Type of entity")] = type_of_entity
    row[cols.index("Regime Name")] = "Test Regime"
    return ",".join(row)


def test_live_sanctions_csv_layout_parses_and_matches():
    text = "\n".join([
        "Report Date: 16-Oct-2026", REAL_HEADER,
        _real_row("TST0001", "EXAMPLOV", "Ivan", "Individual", "dd/mm/1961"),
        _real_row("TST0002", "EXAMPLE TRADING LLC", "", "Entity", type_of_entity="Trading company"),
        _real_row("TST0003", "SEA EXAMPLE", "", "Ship"),
    ])
    sl = parse_csv(text, "test", "test")
    kinds = {e.unique_id: e.kind for e in sl.entries}
    assert kinds == {"TST0001": "individual", "TST0002": "entity", "TST0003": "ship"}
    hits = screen([Screened(name="Ivan EXAMPLOV", role="director", is_person=True, dob_year=1961)], sl)
    assert hits and hits[0].strength == "strong"


def test_officers_are_read_past_the_first_page():
    class Paged:
        calls = []

        async def get(self, path, params=None):
            Paged.calls.append(dict(params or {}))
            start = (params or {}).get("start_index", 0)
            items = [{"name": f"OFFICER {i}", "officer_role": "director"} for i in range(start, min(start + 100, 230))]
            return {"items": items, "total_results": 230}

    r = asyncio.run(CompaniesHouse(Paged()).officers("02366661"))
    assert len(r["items"]) == 230 and r["total_results"] == 230
    assert [c.get("start_index", 0) for c in Paged.calls] == [0, 100, 200]


# ---------------------------------------------------------------- news: grouped findings, collapse guard

def _media(status="active", insolvency=False, rows=None, n=3):
    from datetime import date

    from src.prescreen.checks import CompanyData
    from src.prescreen.media import check_media
    profile = {"company_number": "00000001", "company_name": "EXAMPLE PLC", "company_status": status,
               "date_of_creation": "2000-01-01", "has_insolvency_history": insolvency,
               "links": {"insolvency": "/x"} if insolvency else {}}
    data = CompanyData(number="00000001", profile=profile, today=date(2026, 10, 19))
    results = [{"title": f"Example collapse story {i}", "url": f"https://news.example/{i}", "published": f"2026-0{i+1}-01",
                "source": "news.example", "snippet": "collapse"} for i in range(n)]
    answer = {"items": rows or [{"i": i, "about_this_company": "yes", "category": "insolvency_or_distress",
                                 "severity": "high", "reason": "r"} for i in range(n)],
              "trade_consistency": {"verdict": "not_asked", "reason": ""}}
    llm = JSONChat(None, "groq", "test", "", scripted=[json.dumps(answer)])
    return asyncio.run(check_media(data, results, llm, "", "test"))


def test_many_articles_about_one_event_are_one_finding():
    media, _, items = _media(status="liquidation", insolvency=True, n=7)
    assert len(items) == 7
    high = [f for f in media.findings if f.rule_id == "media_high"]
    assert len(high) == 1 and high[0].text.startswith("7 serious adverse news items")
    assert high[0].route == Route.REFER


def test_collapse_news_about_an_active_company_with_no_insolvency_is_only_possible():
    media, _, items = _media(status="active", insolvency=False, n=2)
    assert all(it.about_this_company == "unsure" for it in items)
    assert not [f for f in media.findings if f.route == Route.REFER]
    assert [f for f in media.findings if f.rule_id == "media_unsure"]


def test_closed_company_reasons_dont_list_its_own_consequences():
    from src.prescreen.models import CheckResult, CheckStatus, Finding
    from src.prescreen.rules import decide
    checks = [
        CheckResult(check_id="status", title="Company status", status=CheckStatus.FLAG, summary="",
                    findings=[Finding(rule_id="company_insolvent", route=Route.OUTSIDE_APPETITE, text="Company is in liquidation.")]),
        CheckResult(check_id="filings", title="Filings", status=CheckStatus.FLAG, summary="",
                    findings=[Finding(rule_id="accounts_overdue_serious", route=Route.REFER, text="Accounts are overdue by 3032 days."),
                              Finding(rule_id="confirmation_overdue", route=Route.REFER, text="Confirmation statement is overdue.")]),
        CheckResult(check_id="insolvency", title="Insolvency", status=CheckStatus.FLAG, summary="",
                    findings=[Finding(rule_id="insolvency_history", route=Route.REFER, text="Insolvency case on record.")]),
    ]
    route, reasons = decide(checks)
    assert route == Route.OUTSIDE_APPETITE
    assert reasons == ["Company is in liquidation.", "Insolvency case on record."]


def test_empty_fallback_after_brave_failed_is_couldnt_check_not_clear():
    # 26 Oct, local Tesco run: Brave failed, GDELT returned nothing, and the check said "No news ... clear".
    from datetime import date

    from src.prescreen.checks import CompanyData
    from src.prescreen.media import check_media
    from src.prescreen.models import CheckStatus
    profile = {"company_number": "00445790", "company_name": "TESCO PLC", "company_status": "active",
               "date_of_creation": "1947-11-27", "links": {}}
    data = CompanyData(number="00445790", profile=profile, today=date(2026, 10, 26))
    llm = JSONChat(None, "groq", "test", "", scripted=[])
    media, _, items = asyncio.run(check_media(data, [], llm, "", "GDELT (free fallback, ~3 months)",
                                              "Brave Search rate limit reached; try again in a minute."))
    assert media.status == CheckStatus.COULDNT_CHECK and items == []
    assert "Brave Search rate limit" in media.summary and "isn't evidence of no news" in media.summary
    # Without a failed main search, an empty result is still an answer (a small company with no news).
    media, _, _ = asyncio.run(check_media(data, [], llm, "", "GDELT (free, ~3 months)"))
    assert media.status == CheckStatus.CLEAR


def test_when_brave_and_gdelt_both_fail_the_reason_names_both():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "0"})

    async def go():
        async with _client(handler) as c:
            try:
                await NewsSearch(c, brave_key="k").search("EXAMPLE LTD")
            except SourceError as err:
                return err
    err = asyncio.run(go())
    assert "Brave Search failed" in err.plain and "GDELT" in err.plain


def test_registered_society_number_is_accepted_and_not_prescreened():
    # 29 Oct: Co-operative Group Limited (IP00525R) failed with "not a valid company number". It's a registered
    # society: Companies House has its profile but not its directors, owners or accounts, so a pre-screen would read
    # the empty registers as findings. The number is accepted, and the run stops with a plain explanation.
    from src.prescreen.identity import normalise_number
    from src.prescreen.service import FixtureTransport
    assert PreScreenRequest(company_number="ip00525r").company_number == "IP00525R"
    assert normalise_number("IP 00525R") == "IP00525R"
    profile = {"company_number": "IP00525R", "company_name": "CO-OPERATIVE GROUP LIMITED",
               "type": "industrial-and-provident-society", "company_status": "active"}
    services = _services("ZZ000001")
    services.ch = CompaniesHouse(FixtureTransport({"/company/IP00525R": profile}))
    with pytest.raises(NotCovered, match="registered society") as err:
        _run("IP00525R", services)
    assert "nothing was checked" in str(err.value) and "mutuals.fca.org.uk" in str(err.value)
    assert isinstance(err.value, CompanyNotFound)  # every caller refunds and shows the message
