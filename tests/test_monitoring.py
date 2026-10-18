"""
Storage and monitoring tests.

Store tests run against JSON files always, and against a real Postgres when
TEST_PG_DSN is set (CI and local runs use a throwaway database with the
migrations in supabase/migrations applied).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.prescreen.monitor import diff, snapshot
from src.prescreen.store import FileStore, PgStore, set_store

PG = os.environ.get("TEST_PG_DSN", "")
MIGRATIONS = sorted(Path(__file__).resolve().parents[1].glob("supabase/migrations/*.sql"))


def _stores(tmp_path):
    out = [FileStore(tmp_path)]
    if PG:
        out.append(PgStore(PG))
    return out


def _report(number="ZZ000009", route="note", rid=None, findings=None, officers=("A, B",), status="active"):
    return {
        "report_id": rid or uuid.uuid4().hex[:16], "created_at": "2026-10-18T10:00:00+00:00",
        "company_number": number, "company_name": "TEST CO LTD", "route": route, "rules_version": "t",
        "meta": {"data": "demo"}, "request": {"company_number": number, "cover_lines": ["property"]},
        "company": {"status": status, "status_detail": "", "last_accounts": {"made_up_to": "2025-12-31"},
                    "officer_names": list(officers), "owner_names": ["OWNER LTD"], "officers_read": True,
                    "owners_read": True},
        "checks": [{"check_id": "insolvency", "title": "Insolvency", "status": "flag" if findings else "clear",
                    "findings": findings or []}],
    }


@pytest.mark.parametrize("which", ["files", "postgres"])
def test_store_round_trip(tmp_path, which):
    stores = {s.kind: s for s in _stores(tmp_path)}
    if which not in stores:
        pytest.skip("no TEST_PG_DSN")
    store = stores[which]

    async def go():
        r = _report(number=f"ZZ{uuid.uuid4().int % 10**6:06d}")
        await store.save_report(r)
        got = await store.load_report(r["report_id"])
        assert got["company_name"] == "TEST CO LTD" and got["decisions"] == []
        for d in ("refer", "proceed"):
            await store.add_decision(r["report_id"], {"decision": d, "reason": "because reasons", "underwriter": "T",
                                                      "overrides_route": False, "recommended_route": "note",
                                                      "rules_version": "t", "recorded_at": "2026-10-18T11:00:00+00:00"})
        got = await store.load_report(r["report_id"])
        assert [d["decision"] for d in got["decisions"]] == ["refer", "proceed"], "append-only"
        recent = await store.recent(50)
        assert any(x["report_id"] == r["report_id"] and x["decision"] == "proceed" for x in recent)
        assert await store.add_decision("0" * 16, {}) is None
        assert await store.load_report("../etc/passwd") is None

        n = r["company_number"]
        await store.watch(n, "TEST CO LTD", r["report_id"], snapshot(r), "note")
        assert (await store.get_watch(n))["active"] is True
        await store.add_changes(n, [{"kind": "status", "severity": "refer", "summary": "x", "before": "a",
                                     "after": "b", "route_before": "note", "route_after": "refer",
                                     "report_id": r["report_id"]}])
        ch = await store.list_changes(number=n)
        assert ch[0]["summary"] == "x" and ch[0]["company_name"] == "TEST CO LTD" and ch[0]["seen"] is False
        await store.mark_seen(n)
        assert (await store.list_changes(number=n))[0]["seen"] is True
        await store.unwatch(n)
        assert n not in [w["company_number"] for w in await store.list_watch()]
    asyncio.run(go())


def test_diff_finds_new_flags_and_people_changes():
    old = snapshot(_report())
    new = snapshot(_report(route="outside_appetite", officers=("A, B", "C, D"),
                           findings=[{"rule_id": "winding_up_petition", "route": "outside_appetite",
                                      "text": "Winding-up petition advertised."}]))
    kinds = {(c["kind"], c["severity"]) for c in diff(old, new)}
    assert ("route", "outside_appetite") in kinds
    assert ("insolvency", "outside_appetite") in kinds
    assert ("officers", "note") in kinds


def test_diff_quiet_when_nothing_changed():
    r = _report()
    assert diff(snapshot(r), snapshot(r)) == []


def test_failed_read_is_not_a_leaver():
    old = snapshot(_report())
    r = _report(officers=())
    r["company"]["officers_read"] = False
    assert not [c for c in diff(old, snapshot(r)) if c["kind"] == "officers"]


def test_watch_then_recheck_records_demo_changes(tmp_path, monkeypatch):
    from src.api.app import app
    from src.prescreen import service
    set_store(FileStore(tmp_path))
    monkeypatch.setattr(service.settings, "use_mock_data", True)
    with TestClient(app) as client:
        rep = client.post("/api/prescreen", json={"company_number": "OC000005",
                                                  "cover_lines": ["professional_indemnity"]}).json()
        assert rep["route"] == "note"
        assert client.post("/api/prescreen/watch", json={"report_id": rep["report_id"]}).status_code == 200
        summary = client.post("/api/jobs/recheck").json()
        assert summary["checked"] == 1 and summary["changed"] == 1
        changes = client.get("/api/prescreen/changes?company_number=OC000005").json()
        text = " ".join(c["summary"] for c in changes)
        assert "Winding-up petition" in text and "KHAN, Bilal" in text and "Harbour Nominees" in text
        assert any(c["kind"] == "route" and c["severity"] == "outside_appetite" for c in changes)
        watch = client.get("/api/prescreen/watch").json()
        assert watch[0]["unseen_changes"] == len(changes)
        assert client.post("/api/jobs/recheck").json()["changes"] == 0, "second run: nothing new"
    set_store(None)


def test_recheck_needs_job_token_in_live_mode(monkeypatch, tmp_path):
    from src.api.app import app
    from src.prescreen import service
    set_store(FileStore(tmp_path))
    monkeypatch.setattr(service.settings, "use_mock_data", False)
    monkeypatch.setenv("PRESCREEN_JOB_TOKEN", "s3cret")
    with TestClient(app) as client:
        assert client.post("/api/jobs/recheck").status_code == 401
        assert client.post("/api/jobs/recheck", headers={"x-job-token": "nope"}).status_code == 401
    set_store(None)
