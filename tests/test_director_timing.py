"""
Rules 19e: a director's link to an insolvent company is weighed by when they left, measured from the date the
insolvency began (Companies House insolvency record).

Found on the live agent run (Monzo, 19 Oct 2026): a director who left SYMBIOCO LTD in May 2022 was counted
against Monzo for an administration that began in Dec 2025. Underwriter reasoning, with the legal anchors:
  in post, or left < 12 months before   counts fully      (s.216 IA 1986: directors in the 12 months before)
  left 12-36 months before              counts, refer max (the IP's conduct report covers 3 years)
  left > 36 months before               not counted, shown
  joined after it began                 not counted, shown
  no start date                         counted, refer max, broker asked for the dates (a gap is never "clear")
  members' voluntary liquidation        not counted (solvent wind-up)
"""

from __future__ import annotations

import asyncio
import copy
from datetime import date

import pytest

from src.prescreen import demo_cases
from src.prescreen.checks import classify_link, insolvency_start
from src.prescreen.engine import Services, run_prescreen
from src.prescreen.llm import JSONChat
from src.prescreen.models import CheckStatus, PreScreenRequest, Route
from src.prescreen.service import DemoTransport
from src.prescreen.sources.companies_house import CompaniesHouse
from src.prescreen.sources.fca import FCARegister
from src.prescreen.sources.gazette import Gazette
from src.prescreen.sources.http import SourceError
from src.prescreen.sources.news import NewsSearch
from src.prescreen.sources.sanctions import parse_csv

ZN = "ZZ000001"
_d = demo_cases._d
_appt = demo_cases._appt
_ins = demo_cases._ins


def _run(appointments: list[dict], records: dict[str, object], name: str = "PENNINE PIES LTD",
         profiles: dict[str, dict] | None = None):
    data: dict = {}
    for c in demo_cases.cases().values():
        data.update(c["ch"])
    data[f"/company/{ZN}"] = {**copy.deepcopy(data[f"/company/{ZN}"]), "company_name": name}
    data["/officers/zzofficer1/appointments"] = {"items": [_appt(ZN, name, "active", _d(213))] + appointments}
    for k in [k for k in data if k.startswith("/company/ZZ9000") and k.endswith("/insolvency")]:
        del data[k]
    for num, rec in records.items():
        data[f"/company/{num}/insolvency"] = rec
    for num, prof in (profiles or {}).items():
        data[f"/company/{num}"] = prof
    services = Services(
        ch=CompaniesHouse(DemoTransport(data)), fca=FCARegister(None, "", "", "", fixtures={}),
        news=NewsSearch(None, fixtures=[]), gazette=Gazette(None, fixtures=[]),
        llm=JSONChat(None, "groq", "test", "", scripted=[]),
        sanctions=parse_csv(demo_cases.FICTIONAL_SANCTIONS_CSV, "test", "test"), today=demo_cases.TODAY)
    return asyncio.run(run_prescreen(PreScreenRequest(company_number=ZN), services))


def _dir_check(report):
    return next(c for c in report.checks if c.check_id == "directors_insolvency")


def _rules(report) -> dict[str, object]:
    return {f.rule_id: f for f in _dir_check(report).findings}


# ---------------------------------------------------------------------------
# The live case and each tier
# ---------------------------------------------------------------------------

def test_left_long_before_the_insolvency_is_not_counted():
    # The Monzo / SYMBIOCO shape: left May 2022, administration began Dec 2025.
    r = _run([_appt("ZZ900002", "SYMBIO EXAMPLE LTD", "administration", "2021-12-23", "2022-05-17")],
             {"ZZ900002": _ins("in-administration", "administration-started-on", "2025-12-19")})
    rules = _rules(r)
    assert "director_insolvent_links" not in rules
    assert "3 years 7 months before" in rules["director_link_not_counted"].text
    assert _dir_check(r).status != CheckStatus.FLAG
    assert r.route == Route.NOTE or "director" not in " ".join(r.route_reasons).lower()


def test_in_post_counts_fully_and_two_are_outside_appetite():
    r = _run([_appt("ZZ900002", "A LTD", "liquidation", "2019-01-01"),
              _appt("ZZ900005", "B LTD", "liquidation", "2019-01-01", "2024-03-01")],
             {"ZZ900002": _ins("creditors-voluntary-liquidation", "wound-up-on", "2025-02-01"),
              "ZZ900005": _ins("compulsory-liquidation", "petitioned-on", "2024-09-01")})
    f = _rules(r)["director_insolvent_links"]
    assert f.route == Route.OUTSIDE_APPETITE and f.detail["in_post"] == 2
    assert "in post when it began" in f.text and "left 6 months before it began" in f.text


def test_left_one_to_three_years_before_counts_but_only_refers():
    r = _run([_appt("ZZ900002", "A LTD", "liquidation", "2018-01-01", "2023-01-01"),
              _appt("ZZ900005", "B LTD", "liquidation", "2018-01-01", "2023-01-01")],
             {"ZZ900002": _ins("creditors-voluntary-liquidation", "wound-up-on", "2024-06-01"),
              "ZZ900005": _ins("creditors-voluntary-liquidation", "wound-up-on", "2025-06-01")})
    f = _rules(r)["director_insolvent_links"]
    assert f.route == Route.REFER and f.detail["near"] == 2 and f.detail["count"] == 2
    assert "left 1 year 5 months before it began" in f.text


def test_unknown_start_date_counts_capped_at_refer_and_asks_the_broker():
    r = _run([_appt("ZZ900002", "A LTD", "liquidation", "2019-01-01"),
              _appt("ZZ900005", "B LTD", "liquidation", "2019-01-01")], {})
    rules = _rules(r)
    assert rules["director_insolvent_links"].route == Route.REFER          # two links, but timing unknown
    assert rules["director_insolvent_links"].detail["unknown"] == 2
    assert "timing unknown" in rules["director_insolvent_links"].text
    assert "director_link_dates_unknown" in rules
    assert "director_link_dates_unknown" in [q["rule_id"] for q in r.broker_questions]


def test_unreadable_insolvency_record_is_unknown_not_clear():
    r = _run([_appt("ZZ900002", "A LTD", "liquidation", "2019-01-01")],
             {"ZZ900002": SourceError("Companies House", "unavailable", "HTTP 503")})
    f = _rules(r)["director_insolvent_links"]
    assert f.route == Route.REFER and "couldn't be read" in f.text


def test_members_voluntary_liquidation_is_solvent_and_not_counted():
    r = _run([_appt("ZZ900002", "HOLDCO EXAMPLE LTD", "liquidation", "2019-01-01")],
             {"ZZ900002": _ins("members-voluntary-liquidation", "declaration-solvent-on", "2025-03-01")})
    rules = _rules(r)
    assert "director_insolvent_links" not in rules
    assert "solvent wind-up" in rules["director_link_not_counted"].text


def test_joined_after_it_began_is_not_counted():
    r = _run([_appt("ZZ900002", "A LTD", "administration", "2025-05-01")],
             {"ZZ900002": _ins("in-administration", "administration-started-on", "2025-01-10")})
    rules = _rules(r)
    assert "director_insolvent_links" not in rules
    assert "after it began" in rules["director_link_not_counted"].text


def test_joined_just_before_is_counted_and_called_a_possible_rescue():
    r = _run([_appt("ZZ900002", "A LTD", "administration", "2024-11-01")],
             {"ZZ900002": _ins("in-administration", "administration-started-on", "2025-01-10")})
    f = _rules(r)["director_insolvent_links"]
    assert f.route == Route.REFER and "possibly a rescue appointment" in f.text


def test_insolvency_older_than_the_lookback_is_not_counted_but_is_shown():
    # Tesco, live: the only director link (in post) began Apr 2021, so the route moved refer -> note.
    r = _run([_appt("ZZ900002", "A LTD", "liquidation", "2012-01-01")],
             {"ZZ900002": _ins("creditors-voluntary-liquidation", "wound-up-on", "2019-03-01")})
    rules = _rules(r)
    assert "director_insolvent_links" not in rules
    assert "began Mar 2019, more than 5 years ago" in rules["director_link_not_counted"].text


def test_old_insolvency_they_had_long_left_is_not_even_mentioned():
    r = _run([_appt("ZZ900002", "A LTD", "liquidation", "2005-01-01", "2010-01-01")],
             {"ZZ900002": _ins("creditors-voluntary-liquidation", "wound-up-on", "2019-03-01")})
    assert "director_link_not_counted" not in _rules(r)


def test_dissolved_company_uses_its_insolvency_start_date():
    prof = {"company_number": "ZZ900006", "company_name": "C LTD", "company_status": "dissolved",
            "date_of_cessation": "2025-08-01", "links": {"insolvency": "/company/ZZ900006/insolvency"}}
    r = _run([_appt("ZZ900006", "C LTD", "dissolved", "2016-01-01", "2021-06-01")],
             {"ZZ900006": _ins("creditors-voluntary-liquidation", "wound-up-on", "2024-02-01")},
             profiles={"ZZ900006": prof})
    f = _rules(r)["director_insolvent_links"]                 # left 32 months before: counts, refer at most
    assert f.route == Route.REFER and f.detail["near"] == 1
    assert "left 2 years 8 months before it began" in f.text


def test_phoenix_with_unknown_timing_refers_not_declines():
    r = _run([_appt("ZZ900001", "PENNINE PIE LIMITED", "liquidation", "2016-05-01", _d(240))], {})
    rules = _rules(r)
    assert rules["phoenix_pattern"].route == Route.REFER and "Timing unknown" in rules["phoenix_pattern"].text


def test_northern_bake_demo_still_declines():
    data: dict = {}
    for c in demo_cases.cases().values():
        data.update(c["ch"])
    services = Services(
        ch=CompaniesHouse(DemoTransport(data)), fca=FCARegister(None, "", "", "", fixtures={}),
        news=NewsSearch(None, fixtures=[]), gazette=Gazette(None, fixtures=[]),
        llm=JSONChat(None, "groq", "test", "", scripted=[]),
        sanctions=parse_csv(demo_cases.FICTIONAL_SANCTIONS_CSV, "test", "test"), today=demo_cases.TODAY)
    r = asyncio.run(run_prescreen(PreScreenRequest(company_number=ZN), services))
    assert r.route == Route.OUTSIDE_APPETITE
    rules = {f.rule_id: f for c in r.checks for f in c.findings}
    assert rules["phoenix_pattern"].route == Route.OUTSIDE_APPETITE
    assert rules["director_insolvent_links"].detail["in_post"] == 3


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resigned,tier", [
    (None, "in_post"), ("2025-06-01", "in_post"), ("2024-07-01", "in_post"),
    ("2023-06-01", "near"), ("2022-07-01", "near"), ("2022-05-01", "long_before"),
])
def test_classify_link_tiers(resigned, tier):
    a = {"appointed_on": "2015-01-01", **({"resigned_on": resigned} if resigned else {})}
    assert classify_link(a, date(2025, 6, 1), None)[0] == tier


def test_insolvency_start_takes_the_earliest_start_date_and_skips_end_dates():
    rec = {"cases": [{"type": "compulsory-liquidation", "dates": [
        {"type": "petitioned-on", "date": "2024-02-01"}, {"type": "wound-up-on", "date": "2024-04-01"},
        {"type": "concluded-winding-up-on", "date": "2025-01-01"}]}]}
    assert insolvency_start(rec) == (date(2024, 2, 1), False)
    assert insolvency_start({"cases": [{"type": "members-voluntary-liquidation", "dates": []}]}) == (None, True)
    assert insolvency_start(None) == (None, False)


def test_llp_broker_questions_say_members():
    from src.prescreen import casefile
    q = casefile.llp_words("New directors joined in the last year. Please confirm the full board.")
    assert q == "New members joined in the last year. Please confirm all the members."
