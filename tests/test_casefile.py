"""
Case-file layout: groups, fraud tags, broker questions, proposal vs record, file note.
None of these may change the route; they only sort and word the result.
"""

from __future__ import annotations

import asyncio
from datetime import date

from src.prescreen import casefile
from src.prescreen.engine import run_prescreen
from src.prescreen.models import PreScreenRequest, Route
from tests.test_prescreen import _all_reviewed, _check, _run, _services


def test_every_check_lands_in_one_of_the_five_groups():
    ids = {g["id"] for g in casefile.GROUPS}
    assert len(ids) == 5
    for number in ("ZZ000001", "ZZ000002", "ZZ000004"):
        report = _run(number)
        for c in report.checks:
            assert c.group in ids, c.check_id
            assert c.check_id in casefile.CHECK_GROUP, f"{c.check_id} has no explicit group"


def test_fraud_is_a_tag_on_the_finding_not_a_group():
    report = _run("ZZ000001")
    phoenix = next(f for c in report.checks for f in c.findings if f.rule_id == "phoenix_pattern")
    assert phoenix.tags == ["phoenix"]
    assert _check(report, "directors_insolvency").group == "management"
    assert "fraud" not in {g["id"] for g in casefile.GROUPS}


def test_grouping_does_not_change_the_route():
    from src.prescreen.rules import decide
    for number in ("ZZ000001", "ZZ000002", "ZZ000004"):
        report = _run(number)
        assert decide(report.checks)[0] == report.route


def test_sanctions_never_become_a_broker_question():
    report = _run("ZZ000004")
    assert any(f.rule_id.startswith("sanctions_") and f.rule_id != "sanctions_discounted"
               for c in report.checks for f in c.findings)
    assert not [q for q in report.broker_questions if q["rule_id"].startswith("sanctions")]
    note = casefile.file_note(report.model_dump(mode="json"))
    assert "Not raised with the broker" in note
    if report.broker_questions:
        assert "HOLD until compliance clears" in note


def test_broker_questions_worst_first_one_per_rule():
    report = _run("ZZ000002")
    qs = report.broker_questions
    assert qs, "an overdue, striking-off company needs questions"
    ranks = [{"note": 0, "refer": 1, "outside_appetite": 2, "compliance_hold": 3}[q["route"]] for q in qs]
    assert ranks == sorted(ranks, reverse=True)
    assert len({q["rule_id"] for q in qs}) == len(qs)
    assert all(q["question"].endswith("?") for q in qs)


def test_every_route_changing_rule_has_a_broker_question_or_goes_to_compliance():
    for number in ("ZZ000001", "ZZ000002", "ZZ000004"):
        report = _run(number)
        for c in report.checks:
            for f in c.findings:
                if f.route != Route.NOTE and f.rule_id not in casefile.COMPLIANCE_ONLY | casefile.OWN_GAPS:
                    assert f.rule_id in casefile.BROKER_QUESTIONS, f.rule_id


def _req(**kw) -> PreScreenRequest:
    return PreScreenRequest(company_number="ZZ000001", **kw)


def _rows(req, company, checks=()):
    return {r["item"]: r for r in casefile.proposal_vs_record(req, company, list(checks), date(2026, 10, 18))}


def test_proposal_vs_record_turnover_against_accounts_size():
    company = {"last_accounts": {"type": "micro-entity", "made_up_to": "2025-12-31"}}
    assert _rows(_req(declared_turnover_gbp=2_400_000), company)["Turnover"]["verdict"] == "differs"
    assert _rows(_req(declared_turnover_gbp=600_000), company)["Turnover"]["verdict"] == "match"
    assert _rows(_req(), company)["Turnover"]["verdict"] == "not_given"


def test_proposal_vs_record_years_trading_longer_than_the_company():
    company = {"incorporated": "2026-03-01"}
    row = _rows(_req(declared_years_trading=12), company)["Years trading"]
    assert row["verdict"] == "differs" and "earlier company" in row["note"]


def test_proposal_vs_record_directors_by_name_order_and_case():
    company = {"officer_names": ["SMITH, Jane Anne", "PATEL, Ravi"], "officers_read": True}
    assert _rows(_req(declared_directors="Jane Smith, Ravi Patel"), company)["Directors"]["verdict"] == "match"
    row = _rows(_req(declared_directors="Jane Smith"), company)["Directors"]
    assert row["verdict"] == "differs" and "PATEL" in row["note"]
    company["officers_read"] = False
    assert _rows(_req(declared_directors="Jane Smith"), company)["Directors"]["verdict"] == "unknown"


def test_report_carries_proposal_rows_and_file_note_lists_gaps():
    req = PreScreenRequest(company_number="ZZ000003", declared_turnover_gbp=500_000)
    report = asyncio.run(run_prescreen(req, _services("ZZ000003")))
    assert {r["item"] for r in report.proposal_vs_record} == {"Trade", "Turnover", "Years trading", "Directors"}
    note = casefile.file_note(report.model_dump(mode="json"))
    assert note.startswith("PRE-SCREEN:")
    if report.couldnt_check:
        assert "Not checked" in note and "not a clear" in note
    for g in casefile.GROUPS:
        if any(c.group == g["id"] for c in report.checks):
            assert g["title"] + ":" in note


def test_file_note_includes_latest_decision():
    payload = _run("ZZ000001").model_dump(mode="json")
    payload["decisions"] = [{"decision": "refer", "reason": "Ask about the earlier bakery.", "underwriter": "TS",
                             "recorded_at": "2026-10-18T20:00:00+00:00", "overrides_route": False}]
    note = casefile.file_note(payload)
    assert "Decision: refer by TS" in note and "earlier bakery" in note


def test_wilko_still_one_model_call_with_casefile():
    assert _run("09563205", llm_answers=[_all_reviewed(4)]).llm_calls == 1


def test_every_refer_or_worse_rule_in_the_source_has_a_question():
    """Reads the checks themselves, so a new route-changing rule can't ship without a broker question."""
    import re
    from pathlib import Path
    src = "".join(Path(f"src/prescreen/{m}.py").read_text() for m in ("checks", "media"))
    pairs = re.findall(r'rule_id="([a-z_]+)",\s*route=Route\.(REFER|OUTSIDE_APPETITE|COMPLIANCE_HOLD)\b', src)
    assert len(pairs) > 20, "regex no longer matches the source; update this test"
    missing = {r for r, _ in pairs} - set(casefile.BROKER_QUESTIONS) - casefile.COMPLIANCE_ONLY - casefile.OWN_GAPS
    assert not missing, f"no broker question for: {sorted(missing)}"


# ---------------------------------------------------------------- trade vs SIC when there's no news

def _trade_answer(verdict: str) -> str:
    import json
    return json.dumps({"items": [], "trade_consistency": {"verdict": verdict, "reason": "test"}})


def test_declared_trade_is_compared_even_with_no_news():
    report = _run("ZZ000002", declared_trade="Bespoke joinery", llm_answers=[_trade_answer("consistent")])
    assert _check(report, "media").summary.startswith("No news")
    assert _check(report, "trade").status.value == "clear"
    assert report.llm_calls == 1
    row = next(r for r in report.proposal_vs_record if r["item"] == "Trade")
    assert row["verdict"] == "match"


def test_trade_mismatch_with_no_news_still_refers():
    report = _run("ZZ000002", declared_trade="Nightclub", llm_answers=[_trade_answer("inconsistent")])
    assert "trade_mismatch" in {f.rule_id for c in report.checks for f in c.findings}


def test_trade_is_couldnt_check_not_missing_when_the_model_fails():
    report = _run("ZZ000002", declared_trade="Bespoke joinery", llm_answers=[])
    trade = _check(report, "trade")
    assert trade.status.value == "couldnt_check"
    assert "Trade" in {r["item"] for r in report.proposal_vs_record}


def test_no_declared_trade_means_no_model_call_when_there_is_no_news():
    assert _run("ZZ000002", llm_answers=[]).llm_calls == 0


def test_an_info_summary_never_says_nothing_was_found():
    """A check that found notes mustn't summarise itself as 'no changes' / 'nothing found'."""
    from src.prescreen import demo_cases
    for number in demo_cases.cases():
        report = _run(number, llm_answers=[_all_reviewed(10)] * 2)
        for c in report.checks:
            if c.status.value == "info" and c.check_id == "changes":
                assert not c.summary.startswith("No "), (number, c.summary)
