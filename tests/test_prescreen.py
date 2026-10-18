"""
Pre-screen engine tests. Real Companies House responses (Wilko, Monzo, captured
18 Oct 2026) plus synthetic cases. No network, no real model.

Each test pins one behaviour the old app got wrong on live data.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.prescreen import demo_cases
from src.prescreen.engine import Services, run_prescreen
from src.prescreen.identity import normalise_name, normalise_number, resolve
from src.prescreen.llm import JSONChat
from src.prescreen.models import CheckStatus, PreScreenRequest, Route
from src.prescreen.service import DemoTransport
from src.prescreen.sources.companies_house import CompaniesHouse, FixtureTransport
from src.prescreen.sources.fca import FCARegister
from src.prescreen.sources.http import SourceError
from src.prescreen.sources.news import NewsSearch, short_name
from src.prescreen.sources.sanctions import parse_csv


def _services(number: str, llm_answers: list[str] | None = None, sanctions=None) -> Services:
    cases = demo_cases.cases()
    ch_data = {}
    for c in cases.values():
        ch_data.update({k.lower() if "?q=" in k else k: v for k, v in c["ch"].items()})
    case = cases.get(number, {})
    return Services(
        ch=CompaniesHouse(DemoTransport(ch_data)),
        fca=FCARegister(None, "", "", "", fixtures={k.lower() if "?q=" in k else k: v
                                                     for k, v in (case.get("fca") or {}).items()}),
        news=NewsSearch(None, fixtures=case.get("news", [])),
        llm=JSONChat(None, "groq", "test", "", scripted=list(llm_answers or [])),
        sanctions=sanctions if sanctions is not None else parse_csv(demo_cases.FICTIONAL_SANCTIONS_CSV, "test", "test"),
        today=demo_cases.TODAY,
    )


def _run(number: str, **kw):
    req = PreScreenRequest(company_number=number, declared_trade=kw.pop("declared_trade", ""),
                           expects_fca_authorisation=kw.pop("expects_fca", False))
    return asyncio.run(run_prescreen(req, _services(number, **kw)))


def _check(report, check_id):
    return next(c for c in report.checks if c.check_id == check_id)


def _rules(report):
    return {f.rule_id for c in report.checks for f in c.findings}


# ---------------------------------------------------------------- identity

def test_number_normalisation():
    assert normalise_number("9563205") == "09563205"
    assert normalise_number(" sc 123456 ") == "SC123456"
    assert normalise_number("Wilko Limited") is None


def test_name_normalisation_treats_ltd_and_limited_alike():
    assert normalise_name("The Wilko Limited") == normalise_name("WILKO LTD")


def test_name_search_with_no_exact_match_does_not_resolve():
    ch = CompaniesHouse(FixtureTransport({"/search/companies?q=apex building services": {"items": [
        {"company_number": "11111111", "title": "APEX BUILDING SERVICES (UK) LTD", "company_status": "active"},
        {"company_number": "22222222", "title": "APEX BUILDING SERVICE LIMITED", "company_status": "dissolved"},
    ]}}))
    result = asyncio.run(resolve(ch, "Apex Building Services"))
    assert not result.resolved
    assert len(result.candidates) == 2
    assert "Pick" in result.message


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


def test_unknown_number_does_not_resolve():
    ch = CompaniesHouse(FixtureTransport({}))
    result = asyncio.run(resolve(ch, "12345678"))
    assert not result.resolved and "no company" in result.message.lower()


# ---------------------------------------------------------------- Wilko (real)

WILKO_LLM = json.dumps({"items": [
    {"i": 3, "about_this_company": "yes", "category": "neutral", "severity": "low",
     "reason": "The Range using the Wilko brand; routine."}],
    "trade_consistency": {"verdict": "not_asked", "reason": ""}})


def test_wilko_news_before_the_rename_is_excluded_without_the_model():
    report = _run("09563205", llm_answers=[WILKO_LLM])
    media = _check(report, "media")
    excluded = [m for m in report.media if m.about_this_company == "no"]
    assert len(excluded) == 3, "the three Aug-Sep 2023 articles predate the Nov 2023 rename"
    assert all("before this company took its current name" in m.reason for m in excluded)
    assert media.status == CheckStatus.CLEAR


def test_wilko_rename_is_shown_and_dissolved_subsidiaries_are_not_held_against_directors():
    report = _run("09563205", llm_answers=[WILKO_LLM])
    assert "renamed_recently" in _rules(report)
    assert "director_insolvent_links" not in _rules(report)
    assert "director_dissolved_context" in _rules(report)
    assert report.route == Route.NOTE


def test_wilko_uses_one_model_call():
    report = _run("09563205", llm_answers=[WILKO_LLM])
    assert report.llm_calls == 1


# ---------------------------------------------------------------- Monzo (real)

def test_monzo_fca_found_and_disciplinary_history_refers():
    llm = json.dumps({"items": [
        {"i": 0, "about_this_company": "yes", "category": "regulatory_action", "severity": "high",
         "reason": "FCA fine for this bank."},
        {"i": 1, "about_this_company": "yes", "category": "neutral", "severity": "low", "reason": "results"}]})
    report = _run("09446231", llm_answers=[llm])
    fca = _check(report, "fca")
    assert fca.status == CheckStatus.FLAG
    assert "fca_authorised" in _rules(report) and "fca_discipline" in _rules(report)
    assert report.route == Route.REFER
    assert any("730427" in s.label for s in fca.sources)


def test_fca_not_applicable_for_unregulated_trade():
    report = _run("ZZ000002")
    assert _check(report, "fca").status == CheckStatus.NOT_APPLICABLE


def test_fca_runs_when_user_says_firm_should_be_authorised():
    report = _run("ZZ000002", expects_fca=True)
    fca = _check(report, "fca")
    assert fca.status != CheckStatus.NOT_APPLICABLE
    assert any(f.route == Route.REFER for f in fca.findings)


# ---------------------------------------------------------------- synthetic routes

def test_phoenix_pattern_is_outside_appetite():
    report = _run("ZZ000001")
    assert report.route == Route.OUTSIDE_APPETITE
    assert {"phoenix_pattern", "director_insolvent_links", "company_new"} <= _rules(report)


def test_namesake_on_sanctions_list_with_different_dob_is_cleared_not_flagged():
    report = _run("ZZ000001")
    sanc = _check(report, "sanctions")
    assert "sanctions_cleared" in {f.rule_id for f in sanc.findings}
    assert sanc.status == CheckStatus.CLEAR


def test_distress_combination_is_outside_appetite():
    report = _run("ZZ000002")
    assert report.route == Route.OUTSIDE_APPETITE
    assert {"strike_off_proposed", "accounts_overdue", "accounts_overdue_serious",
            "confirmation_overdue", "charges_burst"} <= _rules(report)


def test_sanctions_name_and_dob_match_on_psc_is_outside_appetite():
    report = _run("ZZ000004")
    assert report.route == Route.OUTSIDE_APPETITE
    assert "sanctions_strong" in _rules(report)
    assert "psc_overseas_corporate" in _rules(report)


# ---------------------------------------------------------------- couldn't check

def test_sources_down_means_refer_and_every_gap_is_named():
    report = _run("ZZ000003")
    assert report.route == Route.REFER
    for title in ("Directors: disqualification", "Directors: other companies",
                  "Sanctions (UK Sanctions List)", "Ownership (PSC register)", "Adverse media"):
        assert title in report.couldnt_check
    assert any(r.startswith("Couldn't check") for r in report.route_reasons)
    sanc = _check(report, "sanctions")
    assert sanc.status == CheckStatus.COULDNT_CHECK and "directors" in sanc.not_checked_reason


def test_missing_sanctions_list_is_never_clear():
    report = _run("09563205", llm_answers=[WILKO_LLM],
                  sanctions=SourceError("UK Sanctions List", "unavailable", "HTTP 503"))
    assert _check(report, "sanctions").status == CheckStatus.COULDNT_CHECK
    assert report.route == Route.REFER


def test_model_failure_makes_media_couldnt_check():
    report = _run("09446231", llm_answers=["not json", "still not json"])
    media = _check(report, "media")
    assert media.status == CheckStatus.COULDNT_CHECK
    assert "review failed" in media.not_checked_reason


def test_no_model_configured_is_couldnt_check():
    services = _services("09446231")
    services.llm = JSONChat(None, "groq", "m", "")  # no key, not scripted
    report = asyncio.run(run_prescreen(PreScreenRequest(company_number="09446231"), services))
    assert _check(report, "media").status == CheckStatus.COULDNT_CHECK


def test_ccj_always_shown_as_not_checked():
    report = _run("ZZ000002")
    assert _check(report, "ccj").status == CheckStatus.COULDNT_CHECK


def test_unknown_company_number_raises():
    with pytest.raises(ValueError):
        _run("ZZ999999")


# ---------------------------------------------------------------- disqualification lookup

def test_disqualified_director_with_matching_dob_is_outside_appetite():
    cases = demo_cases.cases()
    ch = {k: v for k, v in cases["ZZ000002"]["ch"].items()}
    ch["/search/disqualified-officers?q=sample, morgan"] = {"items": [
        {"title": "Morgan SAMPLE", "date_of_birth": "1971-08-02",
         "links": {"self": "/disqualified-officers/natural/abc123"}}]}
    ch["/disqualified-officers/natural/abc123"] = {"disqualifications": [{"disqualified_until": "2030-01-01"}]}
    services = _services("ZZ000002")
    services.ch = CompaniesHouse(FixtureTransport(ch))
    report = asyncio.run(run_prescreen(PreScreenRequest(company_number="ZZ000002"), services))
    assert "director_disqualified" in _rules(report)


def test_disqualified_namesake_with_other_dob_is_not_flagged():
    cases = demo_cases.cases()
    ch = dict(cases["ZZ000002"]["ch"])
    ch["/search/disqualified-officers?q=sample, morgan"] = {"items": [
        {"title": "Morgan SAMPLE", "date_of_birth": "1950-01-01",
         "links": {"self": "/disqualified-officers/natural/zzz"}}]}
    services = _services("ZZ000002")
    services.ch = CompaniesHouse(FixtureTransport(ch))
    report = asyncio.run(run_prescreen(PreScreenRequest(company_number="ZZ000002"), services))
    assert "director_disqualified" not in _rules(report)
    assert "disq_name_only" in _rules(report)


# ---------------------------------------------------------------- sanctions parser

def test_sanctions_csv_without_expected_columns_is_rejected():
    with pytest.raises(SourceError):
        parse_csv("a,b,c\n1,2,3\n")


def test_sanctions_csv_parses_header_after_metadata_line():
    sl = parse_csv(demo_cases.FICTIONAL_SANCTIONS_CSV)
    assert sl.size == 3
    assert any(e.kind == "individual" for e in sl.entries)


def test_short_name_for_search():
    assert short_name("WILKO LIMITED") == "Wilko"
    assert short_name("Monzo Bank Ltd") == "Monzo Bank"
