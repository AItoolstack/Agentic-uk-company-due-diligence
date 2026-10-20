"""
API for the underwriter pre-screen.

  GET  /api/prescreen/config            what's connected, demo or live, rules version
  GET  /api/prescreen/search?q=         resolve a name or number (identity gate)
  POST /api/prescreen                   run a pre-screen for one company number
  GET  /api/prescreen/{report_id}       fetch a saved report
  GET  /api/prescreen/{report_id}/note       plain-text file note for the underwriting system
  POST /api/prescreen/{report_id}/decision   record the underwriter's decision and reason
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import time
from collections import deque

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.config import settings
from src.prescreen import demo_cases
from src.prescreen.casefile import GROUPS, file_note
from src.prescreen.engine import CompanyNotFound, SourceDown, run_prescreen
from src.prescreen.identity import resolve
from src.prescreen.models import DecisionRequest, IdentityResult, PreScreenRequest
from src.prescreen.rules import RULES_VERSION, THRESHOLDS
from src.prescreen.engine import STAGES
from src.prescreen.service import (DecisionRefused, build_services, demo_mode, list_cases, list_recent, load_report, recheck_all,
                                   record_decision, save_report, watch_from_report)
from src.prescreen.store import get_store, store_status

def require_access(request: Request) -> None:
    """If PRESCREEN_ACCESS_TOKEN is set, every pre-screen API call needs it (header X-Access-Token).
    Reports hold directors' names and birth month/year, so a live deployment should set it."""
    token = os.environ.get("PRESCREEN_ACCESS_TOKEN", "")
    if not token:
        return
    given = request.headers.get("x-access-token", "")
    if not hmac.compare_digest(given, token):
        raise HTTPException(401, "Access code needed.")


router = APIRouter(prefix="/api/prescreen", tags=["prescreen"], dependencies=[Depends(require_access)])

# One shared cap for the whole service: Companies House (600 calls / 5 min), Brave credit and the
# model's tokens-per-minute are shared by everyone, so a per-client limit alone isn't enough.
_RUNS_PER_HOUR = int(os.environ.get("PRESCREEN_RUNS_PER_HOUR", "30"))
_recent: deque = deque()


def _limit() -> None:
    if demo_mode():
        return
    now = time.time()
    while _recent and now - _recent[0] > 3600:
        _recent.popleft()
    if len(_recent) >= _RUNS_PER_HOUR:
        wait = max(1, int((3600 - (now - _recent[0])) // 60) + 1)
        raise HTTPException(429, f"{_RUNS_PER_HOUR} pre-screens an hour on this demo; next slot in {wait} min.")
    _recent.append(now)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, headers={"User-Agent": "ai-toolstack-prescreen/1.0"})


@router.get("/config")
async def config() -> dict:
    live = not demo_mode()
    return {
        "data": "live" if live else "demo",
        "rules_version": RULES_VERSION,
        "access_required": bool(os.environ.get("PRESCREEN_ACCESS_TOKEN")),
        "storage": get_store().kind,
        "storage_status": store_status(),
        "thresholds": THRESHOLDS,
        "groups": GROUPS,
        "sources": {
            "companies_house": bool(settings.companies_house_api_key) or not live,
            "fca_register": bool(settings.fca_api_key and settings.fca_api_email) or not live,
            "news_search": "Brave Search" if settings.brave_search_api_key and live else ("GDELT (free)" if live else "demo"),
            "sanctions": "UK Sanctions List (FCDO)" if live else "fictional demo list",
            "model": settings.chat_model_fast or settings.chat_model,
            "model_configured": bool(settings.groq_api_key or settings.openai_api_key),
        },
        "demo_cases": [] if live else [
            {"company_number": n, "label": c["label"], "kind": c["kind"]} for n, c in demo_cases.cases().items()
        ],
    }


@router.get("/search", response_model=IdentityResult)
async def search(q: str) -> IdentityResult:
    async with _client() as client:
        services, _ = build_services(client)
        return await resolve(services.ch, q)


@router.post("")
async def prescreen(req: PreScreenRequest) -> dict:
    _limit()
    async with _client() as client:
        services, meta = build_services(client, req.company_number)
        try:
            report = await run_prescreen(req, services)
        except CompanyNotFound as err:
            raise HTTPException(404, str(err)) from err
        except SourceDown as err:
            raise HTTPException(503, f"Couldn't start the pre-screen: {err}") from err
    meta["news_queries"] = services.news.queries
    return await save_report(report, meta)


@router.post("/stream")
async def prescreen_stream(req: PreScreenRequest) -> StreamingResponse:
    """Same as POST /api/prescreen, but streams one JSON line per progress event, then the report."""
    _limit()
    queue: asyncio.Queue = asyncio.Queue()

    def progress(stage: str, state: str, detail: str) -> None:
        queue.put_nowait({"type": "progress", "stage": stage, "state": state, "detail": detail})

    async def run() -> None:
        async with _client() as client:
            services, meta = build_services(client, req.company_number)
            try:
                report = await run_prescreen(req, services, progress=progress)
            except CompanyNotFound as err:
                await queue.put({"type": "error", "status": 404, "detail": str(err)})
                return
            except SourceDown as err:
                await queue.put({"type": "error", "status": 503, "detail": f"Couldn't start the pre-screen: {err}"})
                return
            except Exception as err:  # noqa: BLE001
                await queue.put({"type": "error", "status": 500, "detail": f"Unexpected error ({type(err).__name__})."})
                return
        meta["news_queries"] = services.news.queries
        try:
            payload = await save_report(report, meta)
        except Exception as err:  # noqa: BLE001 - a storage outage shouldn't lose the screen itself
            payload = report.model_dump(mode="json")
            payload["meta"] = {**meta, "not_saved": f"couldn't save the report ({type(err).__name__})"}
        await queue.put({"type": "report", "report": payload})

    async def lines():
        yield json.dumps({"type": "stages", "stages": [{"id": i, "label": l} for i, l in STAGES]}) + "\n"
        task = asyncio.create_task(run())
        while True:
            item = await queue.get()
            yield json.dumps(item) + "\n"
            if item["type"] in ("report", "error"):
                break
        await task

    return StreamingResponse(lines(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/recent")
async def recent() -> list[dict]:
    return await list_recent()


@router.get("/cases")
async def cases(filter: str = "all", page: int = 1) -> dict:
    """Recent cases for the Desk: route, coverage, latest decision, cover, run time, stale. 25 a page."""
    return await list_cases(filter, page)


@router.get("/parse")
async def parse(turnover: str = "", years: str = "") -> dict:
    """The proposal fields read the way the pre-screen will read them (the page shows "Read as ..." itself,
    by the same rules; this is for other clients and for checking the two agree)."""
    from src.prescreen.caseview import ParseError, money_words, parse_money, parse_years
    out: dict = {}
    for key, raw, fn in (("turnover", turnover, parse_money), ("years", years, parse_years)):
        if not raw:
            continue
        try:
            v = fn(raw)
            out[key] = {"ok": True, "value": v, "read_as": money_words(v) if key == "turnover" else f"{v}"}
        except ParseError as err:
            out[key] = {"ok": False, "error": str(err)}
    return out


# ---------------------------------------------------------------------------
# Watching
# ---------------------------------------------------------------------------

@router.get("/watch")
async def watch_list() -> list[dict]:
    store = get_store()
    rows = await store.list_watch()
    changes = await store.list_changes(limit=500)
    unseen: dict[str, int] = {}
    for c in changes:
        if not c.get("seen"):
            unseen[c["company_number"]] = unseen.get(c["company_number"], 0) + 1
    return [{k: v for k, v in r.items() if k != "snapshot"} | {"unseen_changes": unseen.get(r["company_number"], 0)}
            for r in rows]


@router.post("/watch")
async def watch_add(body: dict) -> dict:
    report_id = str(body.get("report_id", ""))
    row = await watch_from_report(report_id)
    if row is None:
        raise HTTPException(404, "Report not found.")
    return {k: v for k, v in row.items() if k != "snapshot"}


@router.delete("/watch/{company_number}")
async def watch_remove(company_number: str) -> dict:
    await get_store().unwatch(company_number.upper())
    return {"ok": True}


@router.get("/changes")
async def changes(company_number: str | None = None, limit: int = 30) -> list[dict]:
    return await get_store().list_changes(limit=max(1, min(limit, 200)),
                                          number=company_number.upper() if company_number else None)


@router.post("/changes/{company_number}/seen")
async def changes_seen(company_number: str) -> dict:
    await get_store().mark_seen(company_number.upper())
    return {"ok": True}


# ---------------------------------------------------------------------------
# Agent review (src/agents_v2): specialist agents over the MCP server. They explain; the route stays the rules'.
# ---------------------------------------------------------------------------

@router.post("/eval/legacy")
async def eval_legacy(body: dict) -> dict:
    """Three-way evaluation only: run the old research graph (src/graph.py) on one query with a chosen model and
    return its brief with every model call metered. Same access token and hourly cap as agent reviews."""
    from src.agents_v2.service import AgentLimit, agent_limit
    from src.evaluation.legacy_run import run_legacy
    query = str(body.get("query") or "").strip()
    if not 10 <= len(query) <= 300:
        raise HTTPException(422, "query must be 10-300 characters")
    try:
        agent_limit()
    except AgentLimit as err:
        raise HTTPException(429, str(err)) from err
    out = await run_legacy(query, provider=str(body.get("provider") or ""), model=str(body.get("model") or ""),
                           fast_model=str(body.get("fast_model") or ""),
                           structured_method=str(body.get("structured_method") or ""))
    out["run_id"] = secrets.token_hex(8)
    try:
        await get_store().save_eval_run(out["run_id"], "legacy_graph", str(body.get("label") or query)[:200], out)
    except Exception as err:  # noqa: BLE001 - the result still comes back
        out["not_saved"] = f"{type(err).__name__}"
    return out


@router.get("/agents/config")
async def agents_config() -> dict:
    from src.agents_v2.service import config as agent_config
    return agent_config()


@router.post("/{report_id}/agent-review")
async def agent_review(report_id: str) -> dict:
    """Run the agent layer on a saved report. Takes 20-90 seconds. Returns the review and its trace."""
    from src.agents_v2.model import default_model, provider_key
    from src.agents_v2.service import AgentLimit, agent_limit, run_agent_review
    if await load_report(report_id) is None:
        raise HTTPException(404, "Report not found.")
    if not provider_key(os.environ.get("AGENT_PROVIDER", "openai")):
        raise HTTPException(503, f"The agent model ({default_model()}) has no API key on this server.")
    try:
        agent_limit()
    except AgentLimit as err:
        raise HTTPException(429, str(err)) from err
    return await run_agent_review(report_id=report_id, mcp_url=_agent_mcp_url())


@router.get("/{report_id}/agent-runs")
async def agent_runs(report_id: str, limit: int = 5) -> list[dict]:
    return await get_store().list_agent_runs(report_id, limit=max(1, min(limit, 20)))


def _agent_mcp_url() -> str:
    """On the Space the agents reach the pre-screen over real HTTP MCP, through the same /mcp endpoint an outside
    client uses (and the same access token). Elsewhere, in-process unless AGENT_MCP_URL is set."""
    url = os.environ.get("AGENT_MCP_URL", "")
    if not url and os.environ.get("SPACE_HOST"):
        url = f"http://127.0.0.1:{os.environ.get('PORT', '7860')}/mcp"
    return url


@router.get("/{report_id}")
async def get_report(report_id: str) -> dict:
    payload = await load_report(report_id)
    if payload is None:
        raise HTTPException(404, "Report not found.")
    return payload


@router.get("/{report_id}/note")
async def file_note_text(report_id: str) -> dict:
    """Plain-text file note for the underwriting system, including the latest decision."""
    payload = await load_report(report_id)
    if payload is None:
        raise HTTPException(404, "Report not found.")
    return {"report_id": report_id, "text": file_note(payload)}


@router.post("/{report_id}/decision")
async def decision(report_id: str, req: DecisionRequest) -> dict:
    try:
        entry = await record_decision(report_id, req)
    except DecisionRefused as err:
        raise HTTPException(422, str(err)) from err
    if entry is None:
        raise HTTPException(404, "Report not found.")
    return entry


# ---------------------------------------------------------------------------
# Daily re-check: called by a schedule, protected by its own token.
# ---------------------------------------------------------------------------

jobs_router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@jobs_router.post("/recheck")
async def recheck(request: Request) -> dict:
    token = os.environ.get("PRESCREEN_JOB_TOKEN", "")
    given = request.headers.get("x-job-token", "")
    if not demo_mode() and (not token or not hmac.compare_digest(given, token)):
        raise HTTPException(401, "Job token needed.")
    async with _client() as client:
        return await recheck_all(client)
