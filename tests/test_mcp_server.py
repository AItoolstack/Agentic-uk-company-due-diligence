"""
The pre-screen as an MCP server, tested through a real MCP client session (in-process).

Each test pins one of the design rules in src/prescreen/mcp_server.py:
no decision tool, explicit "couldn't check", bounded outputs, and nothing about a
sanctions match that an agent could pass on to a broker.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp import Client

from src.prescreen import demo_cases
from src.prescreen.mcp_server import server


def _call(name: str, args: dict[str, Any]) -> Any:
    async def go():
        async with Client(server) as c:
            return await c.call_tool(name, args)
    return asyncio.run(go())


def _ok(name: str, args: dict[str, Any]) -> dict[str, Any]:
    r = _call(name, args)
    assert not r.is_error, r.content[0].text if r.content else r
    return r.structured_content


def test_tools_exist_and_none_records_a_decision():
    async def go():
        async with Client(server) as c:
            return (await c.list_tools()).tools
    tools = {t.name: t for t in asyncio.run(go())}
    assert {"find_company", "prescreen_company", "get_report_section", "company_record", "gazette_notices",
            "sanctions_screen", "fca_firm_search", "watch_company", "list_changes"} <= set(tools)
    assert not [n for n in tools if "decision" in n or "decide" in n or "approve" in n]
    for n in ("find_company", "get_report_section", "company_record", "gazette_notices", "sanctions_screen",
              "fca_firm_search", "list_changes"):
        assert tools[n].annotations.read_only_hint is True, n
    assert tools["prescreen_company"].annotations.read_only_hint is False


def test_find_company_never_picks_for_the_agent():
    out = _ok("find_company", {"query": "halden"})
    assert out["resolved"] is False and out["candidates"]
    assert _ok("find_company", {"query": "ZZ000002"})["resolved"] is True


def test_compliance_hold_gives_an_agent_nothing_to_pass_on():
    s = _ok("prescreen_company", {"company_number": "ZZ000004", "declared_trade": "Building materials"})
    assert s["route"] == "compliance_hold"
    assert s["broker_questions"] == [] and s["broker_questions_held"] is True
    rid = s["report_id"]
    texts = [json.dumps(s)] + [json.dumps(_ok("get_report_section", {"report_id": rid, "section": sec}))
                               for sec in ("summary", "checks", "broker_questions", "file_note")]
    for t in texts:
        assert "FICTIONOV" not in t and "FIC0001" not in t
    assert "compliance" in s["route_meaning"].lower()


def test_couldnt_check_is_explicit_and_never_clear():
    s = _ok("prescreen_company", {"company_number": "ZZ000003"})
    assert s["couldnt_check"], "Calder's Companies House outage must show as gaps"
    for gap in s["couldnt_check"]:
        assert gap["reason"] and "not a clear result" in gap["means"]
    assert s["route"] != "note"


def test_summaries_stay_small_for_every_demo_company():
    for number in demo_cases.cases():
        s = _ok("prescreen_company", {"company_number": number})
        assert len(json.dumps(s)) < 8_000, (number, len(json.dumps(s)))
        assert s["decision_needed_from"].startswith("a named underwriter")


def test_bad_number_is_a_plain_tool_error():
    r = _call("prescreen_company", {"company_number": "bad/../1"})
    assert r.is_error
    text = r.content[0].text
    assert "Companies House number" in text and "pydantic" not in text.lower()


def test_unknown_report_is_a_tool_error():
    assert _call("get_report_section", {"report_id": "nope", "section": "summary"}).is_error


def test_sanctions_screen_needs_a_date_of_birth_to_be_strong():
    strong = _ok("sanctions_screen", {"name": "Viktor Ivanovich Fictionov", "dob_year": 1966, "dob_month": 4})
    possible = _ok("sanctions_screen", {"name": "Viktor Ivanovich Fictionov"})
    none = _ok("sanctions_screen", {"name": "Jane Nobody Example"})
    assert strong["result"] == "strong" and possible["result"] == "possible" and none["result"] == "no_match"
    assert "compliance" in strong["handling"]


def test_watch_then_changes():
    s = _ok("prescreen_company", {"company_number": "OC000005"})
    w = _ok("watch_company", {"report_id": s["report_id"]})
    assert w["company_number"] == "OC000005" and "snapshot" not in w
    assert "changes" in _ok("list_changes", {})


def test_rules_resource_and_prompt():
    async def go():
        async with Client(server) as c:
            res = await c.read_resource("prescreen://rules")
            prompt = await c.get_prompt("prescreen_case", {"company": "Halden Joinery"})
            return res, prompt
    res, prompt = asyncio.run(go())
    data = json.loads(res.contents[0].text)
    assert data["routes"]["compliance_hold"] and data["rules_version"]
    assert "don't record a decision" in json.dumps(prompt.model_dump()).lower()


# ---------------------------------------------------------------- over HTTP, mounted on the API

_H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
      "mcp-protocol-version": "2025-11-25"}
_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def _http(headers: dict[str, str], monkeypatch=None):
    from fastapi.testclient import TestClient

    from src.api.app import create_app
    with TestClient(create_app()) as c:
        return c.post("/mcp", headers={**_H, **headers}, json=_LIST)


def test_http_endpoint_lists_tools_without_a_redirect():
    r = _http({})
    assert r.status_code == 200 and "prescreen_company" in r.text


def test_http_endpoint_needs_the_access_token_when_one_is_set(monkeypatch):
    monkeypatch.setenv("PRESCREEN_ACCESS_TOKEN", "s3cret")
    assert _http({}).status_code == 401
    assert _http({"Authorization": "Bearer wrong"}).status_code == 401
    assert _http({"Authorization": "Bearer s3cret"}).status_code == 200
    assert _http({"X-Access-Token": "s3cret"}).status_code == 200


def test_http_endpoint_answers_on_the_space_host_and_refuses_others(monkeypatch):
    monkeypatch.setenv("SPACE_HOST", "aitoolstack-example.hf.space")
    assert _http({"host": "aitoolstack-example.hf.space"}).status_code == 200
    assert _http({"host": "evil.example"}).status_code == 421
