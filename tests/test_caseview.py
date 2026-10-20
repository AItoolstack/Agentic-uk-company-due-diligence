"""The case page's backend (blueprint changes 3-9): news events, sources read, decision rules and history,
proposal parsing, the broker email, recent cases and /case deep links. None of it may change a route."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.prescreen import caseview as V
from src.prescreen.store import FileStore, set_store


# ---------------------------------------------------------------------------
# 6. Proposal parsing: the same table the page's JS is checked against (tests/ui, Playwright)
# ---------------------------------------------------------------------------

MONEY_OK = [("2400000", 2_400_000), ("2,400,000", 2_400_000), ("£2.4m", 2_400_000), ("£2.4M", 2_400_000),
            ("750k", 750_000), ("1.2bn", 1_200_000_000), ("£ 2.4m", 2_400_000), ("2400000.00", 2_400_000),
            ("0", 0), ("£12,500", 12_500), ("3m", 3_000_000)]
MONEY_BAD = ["two million", "2.4.1", "£2,40,000", "2.5", "-5", "£", "2,4m", "1.2345k", "12 34"]


@pytest.mark.parametrize("text,value", MONEY_OK)
def test_parse_money_reads(text, value):
    assert V.parse_money(text) == value


@pytest.mark.parametrize("text", MONEY_BAD)
def test_parse_money_refuses_never_strips(text):
    with pytest.raises(V.ParseError):
        V.parse_money(text)


def test_parse_years_whole_only():
    assert V.parse_years("12") == 12 and V.parse_years("") is None and V.parse_years(3) == 3
    for bad in ("1.5", "two", "1,5", "-1", "400"):
        with pytest.raises(V.ParseError):
            V.parse_years(bad)


def test_request_reads_text_fields_and_refuses_inline():
    from pydantic import ValidationError
    from src.prescreen.models import PreScreenRequest
    r = PreScreenRequest(company_number="09446231", declared_turnover_gbp="£2.4m", declared_years_trading="11")
    assert r.declared_turnover_gbp == 2_400_000 and r.declared_years_trading == 11
    with pytest.raises(ValidationError, match="Whole years"):
        PreScreenRequest(company_number="09446231", declared_years_trading="1.5")
    with pytest.raises(ValidationError, match="for example"):
        PreScreenRequest(company_number="09446231", declared_turnover_gbp="two million")
    with pytest.raises(ValidationError):
        PreScreenRequest(company_number="09446231", rerun_of="not-a-report")


# ---------------------------------------------------------------------------
# 3. News events
# ---------------------------------------------------------------------------

def _item(title, published, about="yes", category="regulatory_action", severity="high", entity="", url=""):
    return {"title": title, "url": url or f"https://x/{abs(hash(title))}", "published": published, "source": "x",
            "about_this_company": about, "entity_named": entity, "category": category, "severity": severity,
            "reason": "r"}


def _report_with_news(media, fca_route="refer"):
    return {
        "company_name": "MONZO BANK LIMITED", "company_number": "09446231", "route": "refer",
        "route_reasons": ["FCA action in the last 5 years: final notice (08/07/2025)."],
        "checks": [
            {"check_id": "fca", "group": "regulatory", "status": "flag", "findings": [
                {"rule_id": "fca_discipline", "route": fca_route, "event_date": "2025-07-08",
                 "text": "FCA action in the last 5 years: final notice (08/07/2025).", "sources": []}]},
            {"check_id": "media", "group": "reputation", "status": "info", "findings": []},
        ],
        "media": media,
    }


def test_news_groups_articles_backing_a_finding_into_one_event():
    media = [
        _item("FCA fines Monzo £21m", "2025-07-08", entity="Monzo Bank Limited"),
        _item("Monzo fined over crime controls", "2025-07-09", about="unsure"),
        _item("Monzo fined again?", "2025-12-01"),  # outside the 14-day window: its own event
        _item("Monzo Bank Europe DAC fined", "2025-07-08", about="related", entity="Monzo Bank Europe DAC"),
        _item("Monzo opens new office", "2025-08-01", category="neutral", severity="low"),
        _item("Monzo the pub", "2024-01-01", about="no"),
        _item("Unread", "2025-01-01", about="not_reviewed", category="", severity="low"),
    ]
    v = V.news_view(_report_with_news(media))
    first = v["events"][0]
    assert first["backs"]["rule_id"] == "fca_discipline" and first["backs"]["reason_index"] == 1
    assert first["article_count"] == 2
    assert [a["names"] for a in first["articles"]] == ["brand", "exact"]
    assert len(v["events"]) == 2
    about_titles = {a["title"] for e in v["sections"]["about"] for a in e["articles"]}
    # A group company's news never appears under "Events about this company".
    assert "Monzo Bank Europe DAC fined" not in about_titles
    assert [i["title"] for i in v["sections"]["group"]] == ["Monzo Bank Europe DAC fined"]
    assert len(v["sections"]["background"]) == 1 and len(v["sections"]["set_aside"]) == 1
    assert len(v["sections"]["not_reviewed"]) == 1
    assert v["summary"].startswith("7 read · 2 events")


def test_unclassified_news_is_not_reviewed_never_background():
    media = [{"title": "t", "url": "u", "published": "2025-01-01", "source": "s", "about_this_company": "unsure",
              "entity_named": "", "category": "", "severity": "low", "reason": ""}]
    v = V.news_view(_report_with_news(media))
    assert v["counts"]["background"] == 0 and v["counts"]["not_reviewed"] == 1


def test_news_with_no_items_is_empty_not_clear():
    v = V.news_view(_report_with_news([]))
    assert v["events"] == [] and v["read"] == 0 and v["summary"].startswith("0 read")


# ---------------------------------------------------------------------------
# 7. Broker email
# ---------------------------------------------------------------------------

def test_broker_email_from_questions():
    r = {"company_name": "MONZO BANK LIMITED", "company_number": "09446231", "route": "refer",
         "request": {"cover_lines": ["management_liability", "cyber"]}, "checks": [],
         "broker_questions": [{"question": "Q one?"}, {"question": "Q two?"}]}
    e = V.broker_email(r)
    assert e["available"] and e["subject"] == "Monzo Bank Limited (09446231): information needed"
    assert "D&O / management liability and cyber" in e["body"] and "1. Q one?\n2. Q two?" in e["body"]
    assert e["body"].startswith("Dear [Broker name],") and e["body"].endswith("[Your name]")
    assert V.broker_email(r, "A. Smith")["body"].endswith("A. Smith")


def test_no_broker_email_on_a_hold():
    r = {"company_name": "X LTD", "company_number": "ZZ000004", "route": "compliance_hold", "checks": [],
         "broker_questions": [{"question": "Q?"}]}
    e = V.broker_email(r)
    assert not e["available"] and e["text"] == "" and "compliance" in e["reason"]


# ---------------------------------------------------------------------------
# 5. Decision rules
# ---------------------------------------------------------------------------

def test_decision_rules():
    ok = "a reason that is long enough"
    assert V.check_decision("refer", "refer", "short", True) == (False, "")
    assert V.check_decision("refer", "proceed", "short", True)[1]  # override needs 20 characters
    assert V.check_decision("refer", "proceed", ok, True) == (True, "")
    assert V.check_decision("refer", "refer", ok, False)[1]  # identity not confirmed
    assert V.check_decision("note", "proceed", "fine", None)[1]  # identity is required from every client
    assert V.check_decision("refer", "decline", "short", True)[1]  # an override needs 20 characters
    assert V.check_decision("compliance_hold", "proceed", ok, True)[1]
    assert V.check_decision("compliance_hold", "compliance", "ref C-1", True) == (False, "")
    assert V.check_decision("compliance_hold", "refer", "ref C-1", True) == (False, "")  # the previous page's word
    assert V.check_decision("note", "compliance", ok, True)[1]
    assert V.expected_decision("outside_appetite") == "decline"


# ---------------------------------------------------------------------------
# Through the API, on the demo cases: every change at once, routes unchanged
# ---------------------------------------------------------------------------

@pytest.fixture
def demo_client(tmp_path, monkeypatch):
    from src.api.app import app
    from src.prescreen import service
    set_store(FileStore(tmp_path))
    monkeypatch.setattr(service.settings, "use_mock_data", True)
    with TestClient(app) as client:
        yield client
    set_store(None)


def test_report_carries_case_fields(demo_client):
    r = demo_client.post("/api/prescreen", json={"company_number": "09446231", "declared_turnover_gbp": "£2.4m",
                                                 "cover_lines": ["management_liability", "cyber"]}).json()
    assert r["route"] == "refer" and r["rules_version"] == "draft-2026-10-20h"
    ids = [s["id"] for s in r["sources_read"]]
    assert ids[:2] == ["companies_house", "directors"] and "news" in ids
    labels = " ".join(s["label"] for s in r["sources_read"])
    assert "web news search" in labels and "Brave" not in labels and "GDELT" not in labels
    assert all(s["read_at"] for s in r["sources_read"])
    assert r["proposal_read"]["turnover"] == "£2,400,000"
    assert r["broker_email"]["available"] and "FCA" in r["broker_email"]["body"]
    assert r["news_view"]["read"] == len(r["media"])
    assert r["expected_decision"] == "refer" and r["stale"] is False
    again = demo_client.get(f"/api/prescreen/{r['report_id']}").json()
    assert again["broker_email"] == r["broker_email"] and again["news_view"]["summary"] == r["news_view"]["summary"]


def test_decision_history_identity_and_override(demo_client):
    r = demo_client.post("/api/prescreen", json={"company_number": "09446231"}).json()
    url = f"/api/prescreen/{r['report_id']}/decision"
    no_id = demo_client.post(url, json={"decision": "refer", "reason": "ok reason", "identity_confirmed": False})
    assert no_id.status_code == 422 and "registered address" in no_id.json()["detail"]
    short = demo_client.post(url, json={"decision": "proceed", "reason": "too short", "identity_confirmed": True})
    assert short.status_code == 422 and "20 characters" in short.json()["detail"]
    a = demo_client.post(url, json={"decision": "refer", "reason": "matches the route", "underwriter": "A. Smith",
                                    "identity_confirmed": True, "overrides_route": True}).json()
    assert a["overrides_route"] is False and a["identity_confirmed"] is True  # the server decides, not the client
    b = demo_client.post(url, json={"decision": "proceed", "underwriter": "A. Smith", "identity_confirmed": True,
                                    "reason": "FCA remediation confirmed by the broker in writing"}).json()
    assert b["overrides_route"] is True
    got = demo_client.get(f"/api/prescreen/{r['report_id']}").json()
    assert [d["decision"] for d in got["decisions"]] == ["refer", "proceed"]  # added, never overwritten
    note = demo_client.get(f"/api/prescreen/{r['report_id']}/note").json()["text"]
    assert "overrides the recommended route" in note and "Identity: company number" in note
    assert "Earlier decisions on this case: 1" in note
    assert got["route"] == "refer"


def test_compliance_hold_only_records_referral_and_has_no_email(demo_client):
    r = demo_client.post("/api/prescreen", json={"company_number": "ZZ000004"}).json()
    assert r["route"] == "compliance_hold" and not r["broker_email"]["available"]
    assert r["expected_decision"] == "compliance"
    url = f"/api/prescreen/{r['report_id']}/decision"
    bad = demo_client.post(url, json={"decision": "decline", "reason": "a long enough reason here",
                                      "identity_confirmed": True})
    assert bad.status_code == 422
    ok = demo_client.post(url, json={"decision": "compliance", "reason": "Referred, ref C-2026-1",
                                     "identity_confirmed": True})
    assert ok.status_code == 200 and ok.json()["overrides_route"] is False
    note = demo_client.get(f"/api/prescreen/{r['report_id']}/note").json()["text"]
    assert "referred to compliance" in note


def test_cases_api_filters_coverage_cover_stale(demo_client, tmp_path):
    a = demo_client.post("/api/prescreen", json={"company_number": "09446231",
                                                 "cover_lines": ["management_liability", "cyber"]}).json()
    demo_client.post("/api/prescreen", json={"company_number": "ZZ000004"})
    c = demo_client.post("/api/prescreen", json={"company_number": "ZZ000001", "rerun_of": a["report_id"]}).json()
    assert c["rerun_of"] == a["report_id"]
    demo_client.post(f"/api/prescreen/{a['report_id']}/decision",
                     json={"decision": "refer", "reason": "matches", "underwriter": "T", "identity_confirmed": True})
    out = demo_client.get("/api/prescreen/cases").json()
    assert out["total"] == 3 and out["counts"] == {"all": 3, "pending": 2, "refer": 1, "outside_appetite": 1,
                                                   "hold": 1}
    row = next(x for x in out["rows"] if x["report_id"] == a["report_id"])
    assert row["cover"] == ["D&O", "Cyber"] and row["decision"]["underwriter"] == "T"
    assert row["coverage"]["completed"] == a["coverage"]["completed"] and row["stale"] is False
    assert [x["route"] for x in demo_client.get("/api/prescreen/cases?filter=hold").json()["rows"]] == \
        ["compliance_hold"]
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    assert V.case_row({"report_id": "x", "created_at": old})["stale"] is True


def test_parse_endpoint_agrees(demo_client):
    out = demo_client.get("/api/prescreen/parse?turnover=£2.4m&years=1.5").json()
    assert out["turnover"]["read_as"] == "£2,400,000" and out["years"]["ok"] is False


def test_case_deep_links_serve_the_page(demo_client):
    for path in ("/case", "/case/0123456789abcdef"):
        res = demo_client.get(path)
        assert res.status_code == 200 and "Company Pre-Screen" in res.text
    home = demo_client.get("/")
    assert home.status_code == 200 and "parseMoney" in home.text  # the case page is the main page now
    old = demo_client.get("/old")
    assert old.status_code == 200 and "parseMoney" not in old.text  # the previous page, kept for comparison
