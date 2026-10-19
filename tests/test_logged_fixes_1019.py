"""Fixes for items logged in the three-way evaluation (19 Oct 2026, docs/rebuild/08)."""

from __future__ import annotations

from datetime import date

from src.prescreen.casefile import broker_questions
from src.prescreen.checks import _finish, insolvency_summary
from src.prescreen.models import CheckStatus, Finding, Route
from tests.test_mcp_server import _ok


# --- Revolut's FCA check: "info" with an empty summary line ----------------------------------------------

def test_info_check_never_has_an_empty_summary():
    r = _finish("fca", "FCA authorisation",
                [Finding(rule_id="fca_authorised", route=Route.NOTE,
                         text="Revolut Ltd, FRN 900562: Registered. Confirm the FRN against the proposal.")],
                "", [], critical=False)
    assert r.status == CheckStatus.INFO
    assert r.summary.startswith("Revolut Ltd, FRN 900562: Registered")


def test_explicit_info_summary_still_wins():
    r = _finish("x", "X", [Finding(rule_id="a", route=Route.NOTE, text="note")], "No changes.", [], False,
                info_summary="One change worth reading.")
    assert r.summary == "One change worth reading."


# --- company_record returns insolvency case dates ---------------------------------------------------------

def test_insolvency_summary_gives_the_start_date_rule_19e_uses():
    rec = {"cases": [{"type": "compulsory-liquidation", "number": "1",
                      "dates": [{"type": "petitioned-on", "date": "2023-03-01"},
                                {"type": "wound-up-on", "date": "2023-05-10"}]}]}
    out = insolvency_summary(rec)
    assert out["began_on"] == "2023-03-01" and out["solvent_wind_up"] is False
    assert out["cases"][0]["dates"][1] == {"type": "wound-up-on", "date": "2023-05-10"}


def test_insolvency_summary_marks_a_members_voluntary_liquidation_as_solvent():
    out = insolvency_summary({"cases": [{"type": "members-voluntary-liquidation", "dates": []}]})
    assert out["solvent_wind_up"] is True and out["began_on"] is None
    assert "solvent" in out["note"]


def test_insolvency_summary_says_when_timing_is_unknown():
    assert insolvency_summary({"cases": [{"type": "administration", "dates": []}]})["note"].startswith("No start date")
    assert insolvency_summary(None)["began_on"] is None


def test_company_record_tool_returns_the_insolvency_record():
    out = _ok("company_record", {"company_number": "ZZ900003"})
    assert out["insolvency"]["began_on"] == "2022-11-01"
    assert out["insolvency"]["cases"][0]["type"] == "creditors-voluntary-liquidation"


def test_company_record_leaves_insolvency_out_when_there_is_none():
    out = _ok("company_record", {"company_number": "ZZ000002"})
    assert "insolvency" not in out


# --- broker question counts people and companies ----------------------------------------------------------

def _links(*people: tuple[str, int]) -> list[dict]:
    return [{"check_id": "directors_insolvency", "findings": [
        {"rule_id": "director_insolvent_links", "route": "refer", "text": f"{n} ...",
         "detail": {"name": n, "count": c}} for n, c in people]}]


def _q(checks, llp=False) -> str:
    return next(q["question"] for q in broker_questions(checks, llp=llp) if q["rule_id"] == "director_insolvent_links")


def test_one_director_one_company_is_singular():
    assert _q(_links(("LAKE, Spencer George", 1))).startswith("A director was involved with a company that went")


def test_one_director_two_companies():
    assert "A director was involved with companies that" in _q(_links(("DE BRUNNER, Maximilian", 2)))


def test_two_directors_one_company_each():
    assert _q(_links(("A", 1), ("B", 1))).startswith("Some directors were involved with companies that")


def test_llp_says_members():
    assert _q(_links(("A", 1)), llp=True).startswith("A member was involved with a company that")


# --- one coverage contract (external review, Monzo: "13 of 13 applied" vs "1 unchecked") ------------------

from src.prescreen.casefile import coverage, file_note  # noqa: E402
from src.agents_v2.specialists import eligible, skipped  # noqa: E402
from src.prescreen.mcp_server import summarise  # noqa: E402


def _c(cid, status, **kw):
    return {"check_id": cid, "title": cid.title(), "status": status, "summary": "", "findings": [], **kw}


MONZO_LIKE = [_c("status", "clear"), _c("fca", "flag"), _c("media", "info"), _c("charges", "info"),
              _c("ccj", "couldnt_check", out_of_scope=True, not_checked_reason="paid source not connected")]


def test_coverage_separates_not_covered_from_couldnt_check():
    cv = coverage(MONZO_LIKE)
    assert (cv["applies"], cv["completed"], cv["couldnt_check"], cv["not_covered"]) == (5, 4, 0, 1)
    assert cv["text"] == "4 of 5 checks completed. Not covered by this tool: Ccj."


def test_coverage_counts_a_failed_source_as_couldnt_check():
    cv = coverage(MONZO_LIKE + [_c("gazette", "couldnt_check", not_checked_reason="timeout"),
                                _c("psc", "not_applicable")])
    assert (cv["applies"], cv["completed"], cv["couldnt_check"], cv["not_covered"], cv["not_applicable"]) == (6, 4, 1, 1, 1)
    assert "Couldn't check: Gazette." in cv["text"]


def test_old_saved_reports_without_the_flag_still_treat_ccj_as_not_covered():
    old = [_c("ccj", "couldnt_check", not_checked_reason="paid source not connected")]
    assert coverage(old)["not_covered"] == 1


def test_live_ccj_check_is_out_of_scope():
    from src.prescreen.checks import check_ccj
    c = check_ccj()
    assert c.out_of_scope and c.status == CheckStatus.COULDNT_CHECK  # never clear


def test_mcp_summary_and_file_note_use_the_same_coverage():
    payload = {"report_id": "r1", "route": "refer", "checks": MONZO_LIKE, "company_number": "09446231",
               "company_name": "MONZO BANK LIMITED", "media": []}
    s = summarise(payload)
    assert s["coverage"]["text"] == coverage(MONZO_LIKE)["text"]
    assert [g["kind"] for g in s["couldnt_check"]] == ["not_covered_by_this_tool"]
    assert coverage(MONZO_LIKE)["text"] in file_note(payload)


# --- the news agent sees serious news that is only a note --------------------------------------------------

def _summary(media, flags=()):
    return summarise({"report_id": "r1", "route": "refer", "checks": [_c("media", "info")], "media": media,
                      "company_number": "1", "company_name": "X"}) | ({"flags": list(flags)} if flags else {})


FCA_NEWS = {"title": "FCA fines Monzo", "about_this_company": "yes", "category": "regulatory_action", "severity": "high"}


def test_news_agent_runs_when_serious_news_is_a_note():
    s = _summary([FCA_NEWS])
    assert s["flags"] == []  # demoted to a note under the FCA check
    assert "news" in eligible(s)
    assert "news" not in skipped(s)


def test_news_agent_runs_for_related_company_news():
    s = _summary([{**FCA_NEWS, "about_this_company": "related"}])
    assert "news" in eligible(s)


def test_skipped_news_never_says_no_finding_when_news_was_read():
    s = _summary([{"title": "Job advert", "about_this_company": "yes", "category": "neutral", "severity": "low"}] * 3)
    assert "news" not in eligible(s)
    assert skipped(s)["news"] == "The pre-screen read 3 news results: nothing adverse to explain."


# --- independent rescoring: Wilko's email said "files as dormant"; its accounts are micro-entity ---------------

def test_dormant_question_says_sic_when_only_the_sic_is_dormant():
    checks = [{"check_id": "filings", "findings": [{"rule_id": "dormant_but_trading", "route": "refer", "text": "",
                                                    "detail": {"basis": "sic"}}]}]
    q = broker_questions(checks)[0]["question"]
    assert "files as dormant" not in q and "SIC 99999" in q


def test_dormant_question_unchanged_when_accounts_are_dormant():
    checks = [{"check_id": "filings", "findings": [{"rule_id": "dormant_but_trading", "route": "refer", "text": "",
                                                    "detail": {"basis": "accounts"}}]}]
    assert broker_questions(checks)[0]["question"].startswith("The company files as dormant")
