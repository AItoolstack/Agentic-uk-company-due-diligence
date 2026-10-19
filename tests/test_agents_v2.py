"""
The multi-agent layer (src/agents_v2), with scripted model replies and the real MCP server in-process.

What these pin:
- the route in the agent review is always the pre-screen's route, for every demo case;
- specialists only run when the case gives them something to do, and never the broker email on a hold;
- the supervisor can drop a specialist but can't add one;
- an agent can't call a tool outside its allowlist; the refusal is in the trace;
- the guardrail blocks the four failure kinds, a blocked answer gets one rewrite, then is withheld;
- every model and tool call is traced with tokens, cost and time.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from src.agents_v2 import guardrail
from src.agents_v2.model import AgentModel
from src.agents_v2.service import run_agent_review
from src.agents_v2.trace import Trace
from src.prescreen import demo_cases


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_MODEL", "gpt-6-luna")
    monkeypatch.delenv("AGENT_MCP_URL", raising=False)
    monkeypatch.delenv("AGENT_GUARDRAIL_JUDGE", raising=False)
    monkeypatch.setenv("PRESCREEN_DATA_DIR", str(tmp_path))
    from src.prescreen import store
    store.set_store(store.FileStore(tmp_path))
    from src.api import prescreen_routes
    prescreen_routes._recent.clear()
    yield
    store.set_store(None)


def run(**kw) -> dict[str, Any]:
    return asyncio.run(run_agent_review(**kw))


def prescreen(number: str, **proposal) -> dict[str, Any]:
    from mcp import Client
    from src.prescreen.mcp_server import server

    async def go():
        async with Client(server) as c:
            return (await c.call_tool("prescreen_company", {"company_number": number, **proposal})).structured_content
    return asyncio.run(go())


def j(obj: Any) -> str:
    return json.dumps(obj)


def plan_all(user: str) -> str:
    eligible = json.loads(user.split("ELIGIBLE SPECIALISTS:\n", 1)[1])
    return j({"run": list(eligible), "focus": {n: "the main finding" for n in eligible}, "dropped": {}})


def polite(agent: str, system: str, user: str) -> str:
    """A well-behaved agent: one tool call, then a short answer built from the case."""
    if agent == "supervisor":
        return plan_all(user)
    first = "RESULT:" not in user
    if agent == "director_history":
        if first:
            return j({"call": {"tool": "company_record", "args": {"company_number": "ZZ900003"}}, "why": "linked"})
        return j({"final": {"directors": [{"name": "Jordan Alex Example", "story":
                 "Was a director of NORTHERN BAKES LIMITED (ZZ900001), now in liquidation, and of EXAMPLE EVENTS "
                 "LTD (ZZ900003), dissolved in Sep 2023 after an insolvency."}],
                 "not_known": ["why the earlier companies failed"]}})
    if agent == "news":
        if first:
            return j({"call": {"tool": "get_report_section", "args": {"section": "news"}}})
        return j({"final": {"items": [], "summary": "Nothing beyond what the pre-screen lists."}})
    if agent == "broker_email":
        if first:
            return j({"call": {"tool": "get_report_section", "args": {"section": "broker_questions"}}})
        return j({"final": {"subject": "Questions on the proposal",
                            "body": "Hello,\n\nCould you help with a few questions before we go further?\n\n"
                                    "Kind regards,\n[Underwriter name]"}})
    raise AssertionError(agent)


# ---------------------------------------------------------------------------
# The supervisor and the route
# ---------------------------------------------------------------------------

def test_review_runs_over_mcp_and_keeps_the_route():
    s = prescreen("ZZ000001", declared_trade="Bakery")
    out = run(report_id=s["report_id"], scripted=polite)
    review = out["review"]
    assert review["route"] == s["route"] == "outside_appetite"
    assert review["route_reasons"] == s["route_reasons"]
    assert review["couldnt_check"] == s["couldnt_check"]
    names = [x["agent"] for x in review["sections"]]
    assert names == ["director_history", "broker_email"]  # no news finding, so no news agent
    assert all(x["status"] == "ok" for x in review["sections"]), review["sections"]
    assert "news" in review["plan"]["not_run"]
    tools = [sp["name"] for sp in out["trace"]["spans"] if sp["kind"] == "tool"]
    assert tools[:2] == ["connect", "get_report_section"]
    assert "company_record" in tools
    assert out["transport"] == "in-process"


def test_route_is_never_changed_for_any_demo_case():
    for number in demo_cases.cases():
        s = prescreen(number, declared_trade="Building contractor")
        out = run(report_id=s["report_id"], scripted=polite)
        assert out["review"]["route"] == s["route"], number
        assert out["review"]["couldnt_check"] == s["couldnt_check"], number


def test_new_case_through_find_and_prescreen():
    out = run(company_query="ZZ000002", proposal={"declared_trade": "Joinery"}, scripted=polite)
    spans = [sp["name"] for sp in out["trace"]["spans"] if sp["kind"] == "tool"]
    assert spans[1:3] == ["find_company", "prescreen_company"]
    assert out["review"]["route"] == "outside_appetite"
    assert out["report_id"]


def test_ambiguous_company_stops_for_a_person():
    out = run(company_query="halden", scripted=polite)
    assert out["status"] == "stopped" and "choose" in out["review"]["stopped"]
    assert not out["review"]["sections"]


def test_compliance_hold_never_starts_the_broker_email():
    s = prescreen("ZZ000004", declared_trade="Building materials")

    def pushy(agent, system, user):
        if agent == "supervisor":
            return j({"run": ["broker_email", "director_history", "news"], "focus": {}, "dropped": {}})
        return polite(agent, system, user)
    out = run(report_id=s["report_id"], scripted=pushy)
    plan = out["review"]["plan"]
    assert plan["run"] == [] and out["trace"]["totals"]["model_calls"] == 0  # nothing eligible: no planner call
    assert "compliance" in plan["not_run"]["broker_email"].lower()
    assert all(x["agent"] != "broker_email" for x in out["review"]["sections"])
    text = j(out)
    assert "FICTIONOV" not in text and "FIC0001" not in text


def test_supervisor_cannot_add_a_specialist():
    s = prescreen("ZZ000001", declared_trade="Bakery")

    def pushy(agent, system, user):
        if agent == "supervisor":
            return j({"run": ["news", "director_history"], "focus": {}, "dropped": {}})
        return polite(agent, system, user)
    plan = run(report_id=s["report_id"], scripted=pushy)["review"]["plan"]
    assert plan["refused_additions"] == ["news"]
    assert "news" not in plan["run"]


def test_supervisor_can_drop_a_specialist_with_a_reason():
    s = prescreen("ZZ000001", declared_trade="Bakery")

    def picky(agent, system, user):
        if agent == "supervisor":
            return j({"run": ["director_history"], "focus": {"director_history": "the phoenix pattern"},
                      "dropped": {"broker_email": "questions can wait for the senior underwriter"}})
        return polite(agent, system, user)
    out = run(report_id=s["report_id"], scripted=picky)
    plan = out["review"]["plan"]
    assert plan["run"] == ["director_history"]
    assert plan["dropped"]["broker_email"].startswith("questions can wait")
    assert plan["focus"]["director_history"] == "the phoenix pattern"


def test_gaps_only_case_runs_no_agents_and_says_why():
    s = prescreen("ZZ000003")  # Calder: Companies House down for officers and owners
    out = run(report_id=s["report_id"], scripted=polite)
    assert out["review"]["sections"] == []
    assert "couldn't be checked" in out["review"]["plan"]["not_run"]["director_history"]
    assert out["review"]["couldnt_check"]
    assert out["trace"]["totals"]["model_calls"] == 0


def test_planner_failure_runs_every_eligible_specialist():
    s = prescreen("ZZ000001", declared_trade="Bakery")

    def broken_planner(agent, system, user):
        if agent == "supervisor":
            return "not json"
        return polite(agent, system, user)
    out = run(report_id=s["report_id"], scripted=broken_planner)
    plan = out["review"]["plan"]
    assert plan["planned_by"].startswith("rules (planner failed")
    assert set(plan["run"]) == {"director_history", "broker_email"}


# ---------------------------------------------------------------------------
# Allowlists, rewrites, withholding
# ---------------------------------------------------------------------------

def test_tool_outside_the_allowlist_is_refused_and_traced():
    s = prescreen("ZZ000001", declared_trade="Bakery")

    def sneaky(agent, system, user):
        if agent == "broker_email" and "RESULT:" not in user:
            return j({"call": {"tool": "sanctions_screen", "args": {"name": "Jordan Example"}}})
        return polite(agent, system, user)
    out = run(report_id=s["report_id"], scripted=sneaky)
    refused = [sp for sp in out["trace"]["spans"] if sp.get("refused")]
    assert refused and refused[0]["name"] == "sanctions_screen" and refused[0]["agent"] == "broker_email"
    email = next(x for x in out["review"]["sections"] if x["agent"] == "broker_email")
    assert email["status"] == "ok"


def test_blocked_answer_is_rewritten_once():
    s = prescreen("ZZ000001", declared_trade="Bakery")
    def careless(agent, system, user):
        if agent == "broker_email" and "A checker blocked it" in user:
            return polite(agent, system, "RESULT:")
        if agent == "broker_email" and "RESULT:" in user:
            return j({"final": {"subject": "Proposal", "body": "No CCJs on record, so we should be fine to "
                                "quote once you reply.\n\nKind regards,\n[Underwriter name]"}})
        return polite(agent, system, user)
    out = run(report_id=s["report_id"], scripted=careless)
    email = next(x for x in out["review"]["sections"] if x["agent"] == "broker_email")
    assert email["status"] == "ok" and email["revisions"] == 1
    rules = {v["rule"] for v in email["blocked_drafts"][0]["violations"]}
    assert {"couldnt_check_as_clear", "route_changed"} <= rules


def test_answer_blocked_twice_is_withheld():
    s = prescreen("ZZ000001", declared_trade="Bakery")

    def stubborn(agent, system, user):
        if agent == "director_history" and ("RESULT:" in user or "A checker blocked it" in user):
            return j({"final": {"directors": [{"name": "J Example", "story":
                                "Also ran EXAMPLE HOLDINGS LTD (ZZ912345), which collapsed owing £2.4m."}]}})
        return polite(agent, system, user)
    out = run(report_id=s["report_id"], scripted=stubborn)
    d = next(x for x in out["review"]["sections"] if x["agent"] == "director_history")
    assert d["status"] == "blocked" and d["text"] == "" and d["revisions"] == 1
    assert "isn't in the evidence" in d["withheld_reason"]
    assert out["status"] == "partial"


def test_judge_findings_block_too():
    s = prescreen("ZZ000001", declared_trade="Bakery")

    def judge(agent, system, user):
        if "director_history" in agent:
            return j({"problems": [{"kind": "allegation_as_fact", "quote": "Was a director",
                                    "why": "test: the judge objects"}]})
        return j({"problems": []})
    out = run(report_id=s["report_id"], scripted=polite, judge_scripted=judge)
    d = next(x for x in out["review"]["sections"] if x["agent"] == "director_history")
    assert d["status"] == "blocked"
    assert d["guardrail"]["checked_by"] == "rules + model judge"


def test_judge_outage_is_shown_not_hidden():
    s = prescreen("ZZ000001", declared_trade="Bakery")
    out = run(report_id=s["report_id"], scripted=polite, judge_scripted=lambda a, s_, u: "garbage")
    for sec in out["review"]["sections"]:
        assert sec["status"] == "ok"
        assert sec["guardrail"]["checked_by"] == "rules only"
        assert sec["guardrail"]["judge"].startswith("unavailable")


# ---------------------------------------------------------------------------
# Trace and storage
# ---------------------------------------------------------------------------

def test_trace_counts_tokens_cost_and_time():
    s = prescreen("ZZ000001", declared_trade="Bakery")
    out = run(report_id=s["report_id"], scripted=polite)
    t = out["trace"]["totals"]
    assert t["model_calls"] >= 5  # plan + 2 per specialist + judge per specialist
    assert t["input_tokens"] > 0 and t["cost_usd"] > 0
    assert set(t["by_agent"]) >= {"supervisor", "director_history", "broker_email"}
    model_spans = [sp for sp in out["trace"]["spans"] if sp["kind"] == "model"]
    assert all(sp["model"] == "gpt-6-luna" for sp in model_spans if sp["agent"] != "guardrail")
    guard = [sp for sp in out["trace"]["spans"] if sp["kind"] == "guardrail"]
    assert len(guard) == 2 and all(sp["passed"] for sp in guard)


def test_runs_are_stored_with_the_report():
    from src.prescreen.store import get_store
    s = prescreen("ZZ000001", declared_trade="Bakery")
    out = run(report_id=s["report_id"], scripted=polite)
    saved = asyncio.run(get_store().list_agent_runs(s["report_id"]))
    assert saved and saved[0]["run_id"] == out["run_id"]


# ---------------------------------------------------------------------------
# Guardrail rules, one by one
# ---------------------------------------------------------------------------

GAPS = [{"check": "County court judgments", "check_id": "ccj"}, {"check": "News", "check_id": "media"}]


@pytest.mark.parametrize("text", [
    "There are no CCJs against the company.",
    "News coverage is clean.",
    "All checks came back clear.",
])
def test_guardrail_blocks_clear_for_a_gap(text):
    assert guardrail.check_clear_claims(text, GAPS)


@pytest.mark.parametrize("text", [
    "County court judgments couldn't be checked (paid source not connected).",
    "News wasn't checked, so there is no news finding either way.",
    "The accounts are clear and up to date.",  # filings isn't a gap here
])
def test_guardrail_allows_honest_gaps(text):
    assert not guardrail.check_clear_claims(text, GAPS)


@pytest.mark.parametrize("text,route", [
    ("This looks safe to quote.", "refer"),
    ("I recommend you decline this risk.", "refer"),
    ("The case is outside appetite.", "refer"),
    ("No referral is needed here.", "refer"),
    ("This should be declined.", "outside_appetite"),
])
def test_guardrail_blocks_route_changes(text, route):
    assert guardrail.check_route(text, route)


def test_guardrail_lets_the_route_be_explained():
    assert not guardrail.check_route("The rules put this outside appetite because of the phoenix pattern.",
                                     "outside_appetite")
    assert not guardrail.check_route("Sales were in decline in 2022.", "refer")


def test_guardrail_sanctions_rules():
    assert guardrail.check_sanctions("We need a compliance check first.", "external", "refer")
    assert guardrail.check_sanctions("Is the owner subject to sanctions?", "external", "refer")
    assert guardrail.check_sanctions("Hello", "external", "compliance_hold")
    assert guardrail.check_sanctions("Matched list entry RUS0123 under the Russia regime.", "internal",
                                     "compliance_hold")
    assert not guardrail.check_sanctions("Please confirm the turnover.", "external", "refer")


def test_guardrail_fact_check():
    ev = j({"text": "EXAMPLE EVENTS LTD (ZZ900003), dissolved 2023-09-14; charge of £250,000; 12% share"})
    assert not guardrail.check_facts("ZZ900003 was dissolved on 14 September 2023.", ev)
    assert not guardrail.check_facts("It was dissolved in Sep 2023 with a £250,000 charge (12%).", ev)
    bad = guardrail.check_facts("ZZ912345 went under in March 2019 owing £2.4m, 40% of turnover.", ev)
    assert {v["detail"].split(" '")[0] for v in bad} == {"company number", "date", "amount", "percentage"}


# ---------------------------------------------------------------------------
# The model client against a fake provider
# ---------------------------------------------------------------------------

def test_model_client_drops_rejected_params_and_records_usage():
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'reasoning_effort'"}})
        return httpx.Response(200, json={"model": "gpt-6-luna-2026-09-30", "choices": [
            {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200,
                      "completion_tokens_details": {"reasoning_tokens": 50}}})

    async def go():
        trace = Trace()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            m = AgentModel(trace, model="gpt-6-luna", provider="openai", api_key="sk-test", client=client)
            assert await m.json("news", "sys", "user") == {"ok": True}
            assert await m.json("news", "sys", "user") == {"ok": True}
        return trace
    trace = asyncio.run(go())
    assert len(seen) == 3 and "reasoning_effort" not in seen[2]  # dropped once, not sent again
    assert "temperature" not in seen[0] and seen[0]["max_completion_tokens"] == 1800
    span = trace.spans[0]
    assert span["input_tokens"] == 1000 and span["output_tokens"] == 200 and span["reasoning_tokens"] == 50
    assert span["cost_usd"] == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1e6)
    assert span["served_by"] == "gpt-6-luna-2026-09-30" and span["dropped_params"] == ["reasoning_effort"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_agent_review_needs_a_report_and_a_key(monkeypatch):
    from fastapi.testclient import TestClient
    from src.api.app import create_app
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("PRESCREEN_ACCESS_TOKEN", raising=False)
    s = prescreen("ZZ000001", declared_trade="Bakery")
    with TestClient(create_app()) as c:
        assert c.post("/api/prescreen/0000000000000000/agent-review").status_code == 404
        r = c.post(f"/api/prescreen/{s['report_id']}/agent-review")
        assert r.status_code == 503 and "gpt-6-luna" in r.json()["detail"]
        assert c.get(f"/api/prescreen/{s['report_id']}/agent-runs").json() == []
        cfg = c.get("/api/prescreen/agents/config").json()
        assert cfg["model"] == "gpt-6-luna" and cfg["available"] is False


def test_api_agent_review_runs_over_http_mcp_on_the_space(monkeypatch):
    from src.api import prescreen_routes
    monkeypatch.delenv("AGENT_MCP_URL", raising=False)
    monkeypatch.setenv("SPACE_HOST", "aitoolstack-example.hf.space")
    assert prescreen_routes._agent_mcp_url() == "http://127.0.0.1:7860/mcp"
    monkeypatch.setenv("AGENT_MCP_URL", "http://example/mcp")
    assert prescreen_routes._agent_mcp_url() == "http://example/mcp"


# ---------------------------------------------------------------------------
# Format drift (found on the first live run: Monzo, director_history never gave a usable final)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reply,kind", [
    ({"final": {"directors": []}}, "final"),
    ({"final": [{"name": "A", "story": "x"}]}, "final"),            # final as a list
    ({"final": '{"directors": []}'}, "final"),                       # final as a JSON string
    ({"directors": [{"name": "A", "story": "x"}]}, "final"),         # no wrapper
    ({"call": {"tool": "company_record", "args": {}}}, "call"),
    ({"tool": "company_record", "args": {"company_number": "1"}}, "call"),  # no "call" wrapper
    ({"answer": "something"}, "bad"),
])
def test_read_turn_accepts_common_drift(reply, kind):
    from src.agents_v2.specialists import read_turn
    assert read_turn("director_history", reply)[0] == kind


def test_unusable_turns_are_traced_with_their_shape():
    s = prescreen("ZZ000001", declared_trade="Bakery")
    turns = {"n": 0}

    def drifting(agent, system, user):
        if agent == "director_history":
            turns["n"] += 1
            if turns["n"] == 1:
                return j({"answer": "I looked and it's fine"})
            return j({"directors": [{"name": "Jordan Alex Example", "story":
                      "Was a director of NORTHERN BAKES LIMITED (ZZ900001), now in liquidation."}]})
        return polite(agent, system, user)
    out = run(report_id=s["report_id"], scripted=drifting)
    d = next(x for x in out["review"]["sections"] if x["agent"] == "director_history")
    assert d["status"] == "ok"
    notes = [sp for sp in out["trace"]["spans"] if sp["kind"] == "note"]
    assert notes and "answer" in notes[0]["error"]
