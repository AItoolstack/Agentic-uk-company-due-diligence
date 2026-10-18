"""
Where reports, decisions, the watchlist and detected changes live.

Two interchangeable stores with the same async methods:
  - PgStore:   Postgres (Supabase), used when SUPABASE_DB_URL is set
  - FileStore: JSON files, used locally and in tests

Decisions are append-only in both: a later decision is added beside the
earlier ones, never over them.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

REPORT_ID = re.compile(r"[0-9a-f]{16}")
ROOT = Path(__file__).resolve().parents[2]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store(Protocol):
    kind: str

    async def save_report(self, payload: dict[str, Any]) -> None: ...
    async def load_report(self, report_id: str) -> dict[str, Any] | None: ...
    async def add_decision(self, report_id: str, entry: dict[str, Any]) -> dict[str, Any] | None: ...
    async def recent(self, limit: int = 12) -> list[dict[str, Any]]: ...
    async def watch(self, number: str, name: str, report_id: str | None, snapshot: dict, route: str) -> None: ...
    async def unwatch(self, number: str) -> None: ...
    async def get_watch(self, number: str) -> dict[str, Any] | None: ...
    async def list_watch(self, active_only: bool = True) -> list[dict[str, Any]]: ...
    async def update_watch(self, number: str, snapshot: dict, route: str) -> None: ...
    async def add_changes(self, number: str, changes: list[dict[str, Any]]) -> None: ...
    async def list_changes(self, limit: int = 30, number: str | None = None) -> list[dict[str, Any]]: ...
    async def mark_seen(self, number: str) -> None: ...
    async def purge(self) -> int: ...


def _summary(payload: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    last = decisions[-1] if decisions else None
    return {
        "report_id": payload.get("report_id"), "company_number": payload.get("company_number"),
        "company_name": payload.get("company_name"), "route": payload.get("route"),
        "created_at": payload.get("created_at"), "decision": last.get("decision") if last else None,
        "data": (payload.get("meta") or {}).get("data", ""),
    }


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def _writable_dir() -> Path:
    preferred = Path(os.environ.get("PRESCREEN_DATA_DIR", ROOT / "output"))
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return preferred
    except OSError:
        return Path(tempfile.gettempdir()) / "prescreen"


class FileStore:
    kind = "files"

    def __init__(self, base: Path | None = None) -> None:
        self.base = base or _writable_dir()
        self.reports = self.base / "prescreens"
        self.watch_file = self.base / "watchlist.json"
        self.changes_file = self.base / "changes.json"

    def _write(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _read(self, path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    async def save_report(self, payload: dict[str, Any]) -> None:
        self._write(self.reports / f"{payload['report_id']}.json", {**payload, "decisions": []})

    async def load_report(self, report_id: str) -> dict[str, Any] | None:
        if not REPORT_ID.fullmatch(report_id or ""):
            return None
        return self._read(self.reports / f"{report_id}.json", None)

    async def add_decision(self, report_id: str, entry: dict[str, Any]) -> dict[str, Any] | None:
        payload = await self.load_report(report_id)
        if payload is None:
            return None
        payload.setdefault("decisions", []).append(entry)
        self._write(self.reports / f"{report_id}.json", payload)
        return entry

    async def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        if not self.reports.exists():
            return []
        files = sorted(self.reports.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
        out = []
        for f in files:
            d = self._read(f, None)
            if d:
                out.append(_summary(d, d.get("decisions") or []))
        return out

    async def watch(self, number, name, report_id, snapshot, route) -> None:
        w = self._read(self.watch_file, {})
        prev = w.get(number) or {}
        w[number] = {"company_number": number, "company_name": name, "added_at": prev.get("added_at") or utcnow_iso(),
                     "added_from_report": report_id, "active": True, "last_checked_at": utcnow_iso(),
                     "last_route": route, "snapshot": snapshot}
        self._write(self.watch_file, w)

    async def unwatch(self, number) -> None:
        w = self._read(self.watch_file, {})
        if number in w:
            w[number]["active"] = False
            self._write(self.watch_file, w)

    async def get_watch(self, number) -> dict[str, Any] | None:
        return self._read(self.watch_file, {}).get(number)

    async def list_watch(self, active_only: bool = True) -> list[dict[str, Any]]:
        rows = list(self._read(self.watch_file, {}).values())
        return [r for r in rows if r.get("active") or not active_only]

    async def update_watch(self, number, snapshot, route) -> None:
        w = self._read(self.watch_file, {})
        if number in w:
            w[number].update(snapshot=snapshot, last_route=route, last_checked_at=utcnow_iso())
            self._write(self.watch_file, w)

    async def add_changes(self, number, changes) -> None:
        if not changes:
            return
        rows = self._read(self.changes_file, [])
        start = max((r["id"] for r in rows), default=0)
        for i, c in enumerate(changes, 1):
            rows.append({"id": start + i, "company_number": number, "detected_at": utcnow_iso(), "seen": False, **c})
        self._write(self.changes_file, rows)

    async def list_changes(self, limit: int = 30, number: str | None = None) -> list[dict[str, Any]]:
        rows = [r for r in self._read(self.changes_file, []) if number is None or r["company_number"] == number]
        names = {w["company_number"]: w["company_name"] for w in await self.list_watch(active_only=False)}
        rows = sorted(rows, key=lambda r: r["id"], reverse=True)[:limit]
        return [{**r, "company_name": names.get(r["company_number"], "")} for r in rows]

    async def mark_seen(self, number) -> None:
        rows = self._read(self.changes_file, [])
        for r in rows:
            if r["company_number"] == number:
                r["seen"] = True
        self._write(self.changes_file, rows)

    async def purge(self) -> int:
        return 0  # local files: nothing to enforce


# ---------------------------------------------------------------------------
# Postgres (Supabase)
# ---------------------------------------------------------------------------

class PgStore:
    kind = "postgres"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def _conn(self):
        import psycopg
        from psycopg.rows import dict_row
        # prepare_threshold=None: works through Supabase's transaction pooler.
        return await psycopg.AsyncConnection.connect(self.dsn, row_factory=dict_row, prepare_threshold=None,
                                                     connect_timeout=10)

    async def _exec(self, sql: str, params: tuple = (), fetch: str = "") -> Any:
        conn = await self._conn()
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                result = None
                if fetch == "one":
                    result = await cur.fetchone()
                elif fetch == "all":
                    result = await cur.fetchall()
            await conn.commit()
            return result
        finally:
            await conn.close()

    @staticmethod
    def _j(value: Any):
        from psycopg.types.json import Jsonb
        return Jsonb(value)

    async def save_report(self, payload: dict[str, Any]) -> None:
        await self._exec(
            "insert into reports (id, created_at, company_number, company_name, route, rules_version, data_mode, payload) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (id) do nothing",
            (payload["report_id"], payload["created_at"], payload["company_number"], payload["company_name"],
             payload["route"], payload["rules_version"], (payload.get("meta") or {}).get("data", "live"),
             self._j(payload)))

    async def load_report(self, report_id: str) -> dict[str, Any] | None:
        if not REPORT_ID.fullmatch(report_id or ""):
            return None
        row = await self._exec("select payload from reports where id = %s", (report_id,), "one")
        if not row:
            return None
        decisions = await self._exec(
            "select decision, reason, underwriter, overrides_route, recommended_route, rules_version, recorded_at "
            "from decisions where report_id = %s order by recorded_at", (report_id,), "all")
        payload = row["payload"]
        payload["decisions"] = [{**d, "recorded_at": d["recorded_at"].isoformat()} for d in decisions]
        return payload

    async def add_decision(self, report_id: str, entry: dict[str, Any]) -> dict[str, Any] | None:
        row = await self._exec("select 1 from reports where id = %s", (report_id,), "one")
        if not row:
            return None
        await self._exec(
            "insert into decisions (report_id, decision, reason, underwriter, overrides_route, recommended_route, "
            "rules_version, recorded_at) values (%s, %s, %s, %s, %s, %s, %s, %s)",
            (report_id, entry["decision"], entry["reason"], entry.get("underwriter", ""), entry["overrides_route"],
             entry["recommended_route"], entry["rules_version"], entry["recorded_at"]))
        return entry

    async def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        rows = await self._exec(
            "select r.id as report_id, r.company_number, r.company_name, r.route, r.created_at, r.data_mode as data, "
            "(select d.decision from decisions d where d.report_id = r.id order by d.recorded_at desc limit 1) as decision "
            "from reports r order by r.created_at desc limit %s", (limit,), "all")
        return [{**r, "created_at": r["created_at"].isoformat()} for r in rows]

    async def watch(self, number, name, report_id, snapshot, route) -> None:
        await self._exec(
            "insert into watchlist (company_number, company_name, added_from_report, active, last_checked_at, "
            "last_route, snapshot) values (%s, %s, %s, true, now(), %s, %s) "
            "on conflict (company_number) do update set active = true, company_name = excluded.company_name, "
            "added_from_report = excluded.added_from_report, last_checked_at = now(), last_route = excluded.last_route, "
            "snapshot = excluded.snapshot",
            (number, name, report_id, route, self._j(snapshot)))

    async def unwatch(self, number) -> None:
        await self._exec("update watchlist set active = false where company_number = %s", (number,))

    async def get_watch(self, number) -> dict[str, Any] | None:
        row = await self._exec("select * from watchlist where company_number = %s", (number,), "one")
        return self._iso(row) if row else None

    async def list_watch(self, active_only: bool = True) -> list[dict[str, Any]]:
        rows = await self._exec("select * from watchlist" + (" where active" if active_only else "") +
                                " order by added_at desc", (), "all")
        return [self._iso(r) for r in rows]

    async def update_watch(self, number, snapshot, route) -> None:
        await self._exec("update watchlist set snapshot = %s, last_route = %s, last_checked_at = now() "
                         "where company_number = %s", (self._j(snapshot), route, number))

    async def add_changes(self, number, changes) -> None:
        for c in changes:
            await self._exec(
                "insert into changes (company_number, kind, severity, summary, before, after, route_before, route_after, "
                "report_id) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (number, c["kind"], c["severity"], c["summary"], self._j(c.get("before")), self._j(c.get("after")),
                 c.get("route_before"), c.get("route_after"), c.get("report_id")))

    async def list_changes(self, limit: int = 30, number: str | None = None) -> list[dict[str, Any]]:
        sql = ("select c.*, w.company_name from changes c join watchlist w using (company_number) " +
               ("where c.company_number = %s " if number else "") + "order by c.detected_at desc limit %s")
        rows = await self._exec(sql, ((number, limit) if number else (limit,)), "all")
        return [self._iso(r) for r in rows]

    async def mark_seen(self, number) -> None:
        await self._exec("update changes set seen = true where company_number = %s", (number,))

    async def purge(self) -> int:
        row = await self._exec("select purge_expired_reports() as n", (), "one")
        return int(row["n"]) if row else 0

    @staticmethod
    def _iso(row: dict[str, Any]) -> dict[str, Any]:
        return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}


_STORE: Store | None = None


def get_store() -> Store:
    global _STORE
    if _STORE is None:
        dsn = os.environ.get("SUPABASE_DB_URL", "").strip()
        _STORE = PgStore(dsn) if dsn else FileStore()
    return _STORE


def set_store(store: Store | None) -> None:
    """Tests: swap the store."""
    global _STORE
    _STORE = store
