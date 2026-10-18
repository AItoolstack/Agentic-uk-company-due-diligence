"""
Noise found on the second live batch (19 Oct 2026), each pinned with a small hand-built case.
None of these may change the route; they only stop the same fact being said twice.
"""

from __future__ import annotations

from src.prescreen import casefile
from src.prescreen.models import CheckResult, CheckStatus, Finding, Route
from src.prescreen.rules import decide


def _c(check_id: str, *findings: Finding, status: CheckStatus = CheckStatus.FLAG) -> CheckResult:
    return CheckResult(check_id=check_id, title=check_id, status=status, summary="", findings=list(findings))


def _f(rule_id: str, route: Route, text: str = "", **detail) -> Finding:
    return Finding(rule_id=rule_id, route=route, text=text or rule_id, detail=detail)


def test_several_directors_with_insolvent_companies_are_one_reason():
    checks = [_c("directors_insolvency",
                 _f("director_insolvent_links", Route.REFER, "long line A", count=4, name="DE BRUNNER, Aidan"),
                 _f("director_insolvent_links", Route.REFER, "long line B", count=1, name="GETHING, Julian"),
                 _f("director_insolvent_links", Route.REFER, "long line C", count=1, name="PEARSON, Ian"))]
    route, reasons = decide(checks)
    assert route == Route.REFER
    assert len(reasons) == 1
    assert reasons[0].startswith("3 directors") and "De Brunner (4)" in reasons[0] and "(6 in all)" in reasons[0]


def test_one_director_keeps_the_full_line():
    route, reasons = decide([_c("directors_insolvency",
                                _f("director_insolvent_links", Route.REFER, "full line", count=1, name="X, Y"))])
    assert reasons == ["full line"]


def test_grouping_keeps_the_worst_route():
    checks = [_c("directors_insolvency",
                 _f("director_insolvent_links", Route.OUTSIDE_APPETITE, "a", count=3, name="A, B"),
                 _f("director_insolvent_links", Route.REFER, "b", count=1, name="C, D"))]
    assert decide(checks)[0] == Route.OUTSIDE_APPETITE


def test_dormant_but_trading_and_trade_mismatch_are_one_reason_and_one_question():
    checks = [_c("filings", _f("dormant_but_trading", Route.REFER, "dormant but trading")),
              _c("trade", _f("trade_mismatch", Route.REFER, "trade doesn't match SIC 99999"))]
    assert decide(checks)[1] == ["dormant but trading"]
    rules = [q["rule_id"] for q in casefile.broker_questions(checks)]
    assert "dormant_but_trading" in rules and "trade_mismatch" not in rules


def test_ended_company_gets_only_the_questions_that_still_matter():
    checks = [_c("status", _f("company_insolvent", Route.OUTSIDE_APPETITE)),
              _c("filings", _f("accounts_overdue_serious", Route.REFER), _f("confirmation_overdue", Route.REFER)),
              _c("insolvency", _f("insolvency_history", Route.REFER)),
              _c("ownership", _f("no_psc", Route.REFER)),
              _c("media", _f("media_high", Route.REFER))]
    assert [q["rule_id"] for q in casefile.broker_questions(checks)] == ["company_insolvent"]


def test_questions_are_capped_and_notes_only_fill_spare_places():
    checks = [_c("a", *[_f(r, Route.REFER) for r in ("accounts_overdue", "charges_burst", "no_psc",
                                                     "confirmation_overdue", "insolvency_history", "trade_mismatch")]),
              _c("changes", _f("officers_joined", Route.NOTE), _f("officers_left", Route.NOTE),
                 status=CheckStatus.INFO)]
    qs = casefile.broker_questions(checks)
    assert len(qs) == casefile.MAX_QUESTIONS
    assert all(q["route"] != "note" for q in qs)
    few = casefile.broker_questions([_c("a", _f("charges_burst", Route.REFER)),
                                     _c("changes", _f("officers_joined", Route.NOTE), status=CheckStatus.INFO)])
    assert [q["rule_id"] for q in few] == ["charges_burst", "officers_joined"]


def test_news_of_an_fca_action_already_found_is_a_note_not_a_second_reason():
    from tests.test_prescreen import MONZO_LLM, _run
    report = _run("09446231", llm_answers=[MONZO_LLM], cover_lines=["management_liability"])
    media = [f for c in report.checks for f in c.findings if f.rule_id == "media_high"]
    assert media and all(f.route == Route.NOTE and f.text.startswith("News coverage of the FCA action") for f in media)
    assert any(f.rule_id == "fca_discipline" and f.route == Route.REFER for c in report.checks for f in c.findings)
    assert report.route == Route.REFER
    assert not [r for r in report.route_reasons if r.startswith("News coverage")]


def test_serious_and_medium_news_ask_one_question():
    checks = [_c("media", _f("media_high", Route.REFER), _f("media_medium", Route.NOTE))]
    assert [q["rule_id"] for q in casefile.broker_questions(checks)] == ["media_high"]
