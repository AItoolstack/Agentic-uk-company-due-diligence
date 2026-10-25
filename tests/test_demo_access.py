"""
Demo access (src/prescreen/access.py): owner, guest codes, public examples, daily allowances, and the rule
that a guest is never put on a paid model.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.prescreen import access

OWNER = "owner-code-for-tests"
GUEST = "guest-code-for-tests"
OTHER = "other-guest-code"


@pytest.fixture
def env(monkeypatch, tmp_path):
    from src.prescreen.store import FileStore, set_store
    set_store(FileStore(tmp_path))
    access.reset_allowances()
    monkeypatch.setenv("PRESCREEN_ACCESS_TOKEN", OWNER)
    monkeypatch.setenv("PRESCREEN_GUEST_CODES",
                       f"acme:{access.hash_code(GUEST)}:2099-12-31,beta:{access.hash_code(OTHER)}:2099-12-31")
    monkeypatch.setenv("GUEST_PRESCREENS_PER_DAY", "2")
    monkeypatch.setenv("GUEST_AGENT_REVIEWS_PER_DAY", "1")
    monkeypatch.delenv("PRESCREEN_PUBLIC_EXAMPLES", raising=False)
    monkeypatch.delenv("GUEST_AGENT_PROVIDER", raising=False)
    yield monkeypatch
    access.reset_allowances()


def _client():
    from src.api.app import create_app
    return TestClient(create_app())


def _run(c, code, number="ZZ000001"):
    return c.post("/api/prescreen", json={"company_number": number}, headers={"X-Access-Token": code})


# ---------------------------------------------------------------------------
# Who is calling
# ---------------------------------------------------------------------------

def test_open_when_nothing_is_configured(monkeypatch):
    for k in ("PRESCREEN_ACCESS_TOKEN", "PRESCREEN_GUEST_CODES", "PRESCREEN_PUBLIC_EXAMPLES"):
        monkeypatch.delenv(k, raising=False)
    assert access.resolve("").role == "owner"


def test_owner_guest_wrong_and_expired(env):
    assert access.resolve(OWNER).role == "owner"
    g = access.resolve(GUEST)
    assert (g.role, g.label, g.run_by) == ("guest", "acme", "guest:acme")
    with pytest.raises(access.AccessDenied, match="wasn't recognised"):
        access.resolve("nope")
    with pytest.raises(access.AccessDenied, match="Access code needed"):
        access.resolve("")
    with pytest.raises(access.AccessDenied, match="ended on 31 Dec 2099"):
        access.resolve(GUEST, today=date(2100, 1, 1))


def test_guest_codes_are_stored_hashed_and_malformed_entries_ignored(env):
    env.setenv("PRESCREEN_GUEST_CODES", f"acme:{GUEST}:2099-12-31,bad,x:{access.hash_code(OTHER)}:2099-01-01")
    assert [g["label"] for g in access.guest_codes()] == ["x"]  # a plain-text code is not accepted as a hash
    with pytest.raises(access.AccessDenied):
        access.resolve(GUEST)


def test_public_only_when_examples_are_set(env):
    env.setenv("PRESCREEN_PUBLIC_EXAMPLES", "0123456789abcdef, not-an-id")
    assert access.public_examples() == ["0123456789abcdef"]
    assert access.resolve("").role == "public"


# ---------------------------------------------------------------------------
# Free models only for guests
# ---------------------------------------------------------------------------

def test_guest_agent_model_refuses_a_paid_provider(env):
    assert access.guest_agent_model() == {"provider": "groq", "model": "openai/gpt-oss-120b", "judge": True}
    env.setenv("GUEST_AGENT_PROVIDER", "openai")
    with pytest.raises(access.PaidModelRefused):
        access.guest_agent_model()


def test_guest_news_model_moves_off_a_paid_provider():
    assert access.guest_news_model("openai", "gpt-6-luna") == ("groq", "openai/gpt-oss-20b")
    assert access.guest_news_model("groq", "openai/gpt-oss-20b") == ("groq", "openai/gpt-oss-20b")


def test_build_services_uses_a_free_model_for_guests(env):
    import httpx
    from src.config import settings
    from src.prescreen.service import build_services
    env.setattr(settings, "chat_model_provider", "openai")
    env.setattr(settings, "chat_model_fast", "gpt-6-luna")
    client = httpx.AsyncClient()
    owner, _ = build_services(client, "ZZ000001")
    guest, _ = build_services(client, "ZZ000001", free_only=True)
    assert owner.llm.provider == "openai"
    assert guest.llm.provider == "groq" and guest.llm.model == "openai/gpt-oss-20b"


# ---------------------------------------------------------------------------
# What each caller sees and may do (API)
# ---------------------------------------------------------------------------

def test_guest_sees_own_cases_and_examples_only(env):
    with _client() as c:
        mine = _run(c, GUEST).json()
        theirs = _run(c, OTHER, "ZZ000002").json()
        owners = _run(c, OWNER, "ZZ000003").json()
        assert mine["meta"]["run_by"] == "guest:acme"
        h = {"X-Access-Token": GUEST}
        ids = {r["report_id"] for r in c.get("/api/prescreen/cases", headers=h).json()["rows"]}
        assert ids == {mine["report_id"]}
        assert c.get(f"/api/prescreen/{theirs['report_id']}", headers=h).status_code == 404
        assert c.get(f"/api/prescreen/{owners['report_id']}/note", headers=h).status_code == 404
        assert c.get(f"/api/prescreen/{mine['report_id']}", headers=h).json()["read_only"] is False
        owner_ids = {r["report_id"] for r in c.get("/api/prescreen/cases", headers={"X-Access-Token": OWNER}).json()["rows"]}
        assert owner_ids == {mine["report_id"], theirs["report_id"], owners["report_id"]}
        assert {r["report_id"] for r in c.get("/api/prescreen/recent", headers=h).json()} == {mine["report_id"]}


def test_examples_are_public_and_read_only(env):
    with _client() as c:
        ex = _run(c, OWNER).json()
        env.setenv("PRESCREEN_PUBLIC_EXAMPLES", ex["report_id"])
        cfg = c.get("/api/prescreen/config").json()
        assert cfg["caller"]["role"] == "public" and cfg["examples"][0]["report_id"] == ex["report_id"]
        assert c.get(f"/api/prescreen/{ex['report_id']}").json()["read_only"] is True
        assert c.get(f"/api/prescreen/{ex['report_id']}/note").status_code == 200
        assert [r["report_id"] for r in c.get("/api/prescreen/cases").json()["rows"]] == [ex["report_id"]]
        assert _run(c, "").status_code == 401  # running needs a code
        assert c.get("/api/prescreen/search", params={"q": "halden"}).status_code == 401
        d = {"decision": "refer", "reason": "Trying the demo out", "underwriter": "Guest", "identity_confirmed": True}
        assert c.post(f"/api/prescreen/{ex['report_id']}/decision", json=d).status_code == 403
        assert c.post(f"/api/prescreen/{ex['report_id']}/decision", json=d,
                      headers={"X-Access-Token": GUEST}).status_code == 403
        assert c.post(f"/api/prescreen/{ex['report_id']}/agent-review",
                      headers={"X-Access-Token": GUEST}).status_code == 403


def test_owner_only_parts(env):
    with _client() as c:
        h = {"X-Access-Token": GUEST}
        assert c.get("/api/prescreen/watch", headers=h).status_code == 403
        assert c.post("/api/prescreen/eval/legacy", json={"query": "x" * 20}, headers=h).status_code == 403
        assert c.get("/api/prescreen/changes", headers=h).json() == []
        assert c.get("/api/prescreen/watch", headers={"X-Access-Token": OWNER}).status_code == 200


def test_guest_daily_allowance_and_refund(env):
    with _client() as c:
        assert _run(c, GUEST, "99999999").status_code == 404  # not found: given back
        assert _run(c, GUEST).status_code == 200
        assert _run(c, GUEST, "ZZ000002").status_code == 200
        r = _run(c, GUEST, "ZZ000003")
        assert r.status_code == 429 and "used its 2 pre-screens" in r.json()["detail"]
        assert _run(c, OTHER, "ZZ000003").status_code == 200  # another code has its own allowance
        assert _run(c, OWNER, "ZZ000003").status_code == 200  # the owner isn't counted
        cfg = c.get("/api/prescreen/config", headers={"X-Access-Token": GUEST}).json()
        assert cfg["caller"]["allowance"]["prescreen"]["used"] == 2


def test_service_wide_guest_cap(env):
    env.setenv("GUEST_TOTAL_PRESCREENS_PER_DAY", "1")
    with _client() as c:
        assert _run(c, GUEST).status_code == 200
        r = _run(c, OTHER, "ZZ000002")
        assert r.status_code == 429 and "try again tomorrow" in r.json()["detail"]


def test_guest_agent_review_runs_on_the_free_model(env):
    from src.agents_v2 import service as agent_service
    seen = {}

    async def fake(**kw):
        seen.update(kw)
        return {"ok": True}

    env.setattr(agent_service, "run_agent_review", fake)
    env.setenv("GROQ_API_KEY", "test-key")
    env.delenv("OPENAI_API_KEY", raising=False)
    with _client() as c:
        mine = _run(c, GUEST).json()
        h = {"X-Access-Token": GUEST}
        assert c.post(f"/api/prescreen/{mine['report_id']}/agent-review", headers=h).status_code == 200
        assert (seen["provider"], seen["model_name"], seen["run_by"]) == ("groq", "openai/gpt-oss-120b", "guest:acme")
        r = c.post(f"/api/prescreen/{mine['report_id']}/agent-review", headers=h)
        assert r.status_code == 429 and "agent reviews" in r.json()["detail"]
        cfg = c.get("/api/prescreen/agents/config", headers=h).json()
        assert cfg["provider"] == "groq" and cfg["available"] is True


def test_guest_agent_review_never_falls_back_to_paid(env):
    env.setenv("GUEST_AGENT_PROVIDER", "openai")
    env.setenv("OPENAI_API_KEY", "paid-key")
    with _client() as c:
        mine = _run(c, GUEST).json()
        r = c.post(f"/api/prescreen/{mine['report_id']}/agent-review", headers={"X-Access-Token": GUEST})
        assert r.status_code == 503
        env.setenv("GUEST_AGENT_PROVIDER", "groq")
        env.delenv("GROQ_API_KEY", raising=False)
        r = c.post(f"/api/prescreen/{mine['report_id']}/agent-review", headers={"X-Access-Token": GUEST})
        assert r.status_code == 503  # no free key: refused, not moved onto OpenAI


def test_demonstration_line_on_guest_live_runs():
    from src.prescreen.casefile import file_note
    base = {"company_name": "X LTD", "company_number": "1", "report_id": "a" * 16, "route": "refer",
            "rules_version": "v", "created_at": "2026-10-20T10:00:00"}
    note = file_note({**base, "meta": {"data": "live", "demonstration": True}})
    assert "DEMONSTRATION RUN" in note
    assert "DEMONSTRATION RUN" not in file_note({**base, "meta": {"data": "live"}})


def test_guest_live_runs_are_marked_as_demonstrations():
    from src.api.prescreen_routes import _run_meta
    assert _run_meta(access.Caller("guest", "acme"), {"data": "live"}) == {
        "data": "live", "run_by": "guest:acme", "demonstration": True}
    assert _run_meta(access.Caller("guest", "acme"), {"data": "demo"}) == {"data": "demo", "run_by": "guest:acme"}
    assert _run_meta(access.Caller("owner"), {"data": "live"}) == {"data": "live", "run_by": "owner"}


def test_owner_can_preview_the_guest_model(env):
    from src.agents_v2 import service as agent_service
    seen = {}

    async def fake(**kw):
        seen.update(kw)
        return {"ok": True}

    env.setattr(agent_service, "run_agent_review", fake)
    env.setenv("GROQ_API_KEY", "test-key")
    with _client() as c:
        rid = _run(c, OWNER).json()["report_id"]
        h = {"X-Access-Token": OWNER}
        assert c.post(f"/api/prescreen/{rid}/agent-review?model=guest", headers=h).status_code == 200
        assert (seen["provider"], seen["run_by"]) == ("groq", "owner")
        seen.clear()
        env.setenv("OPENAI_API_KEY", "paid-key")
        assert c.post(f"/api/prescreen/{rid}/agent-review", headers=h).status_code == 200
        assert "provider" not in seen  # the owner's normal run uses AGENT_PROVIDER


def test_legacy_research_and_mcp_are_owner_only(env):
    with _client() as c:
        body = {"query": "Research Example Holdings Ltd please"}
        for path in ("/research/sync", "/research/stream", "/research/chat"):
            assert c.post(path, json=body, headers={"X-Access-Token": GUEST}).status_code == 403
            assert c.post(path, json=body).status_code == 401
        assert c.post("/mcp", json={}, headers={"X-Access-Token": GUEST}).status_code == 401
        env.delenv("PRESCREEN_ACCESS_TOKEN")
        assert c.post("/mcp", json={}).status_code == 401  # guests configured, no owner code: closed


def test_guest_agent_model_allows_openrouter_never_openai(env):
    env.setenv("GUEST_AGENT_PROVIDER", "openrouter")
    assert access.guest_agent_model()["provider"] == "openrouter"
    for value in ("openai", "off", ""):
        env.setenv("GUEST_AGENT_PROVIDER", value)
        with pytest.raises(access.PaidModelRefused):
            access.guest_agent_model()
