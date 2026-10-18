"""
API for the underwriter pre-screen.

  GET  /api/prescreen/config            what's connected, demo or live, rules version
  GET  /api/prescreen/search?q=         resolve a name or number (identity gate)
  POST /api/prescreen                   run a pre-screen for one company number
  GET  /api/prescreen/{report_id}       fetch a saved report
  POST /api/prescreen/{report_id}/decision   record the underwriter's decision and reason
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
from collections import deque

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.config import settings
from src.prescreen import demo_cases
from src.prescreen.engine import CompanyNotFound, SourceDown, run_prescreen
from src.prescreen.identity import resolve
from src.prescreen.models import DecisionRequest, IdentityResult, PreScreenRequest
from src.prescreen.rules import RULES_VERSION, THRESHOLDS
from src.prescreen.engine import STAGES
from src.prescreen.service import (build_services, demo_mode, list_recent, load_report, record_decision,
                                   save_report)

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
        raise HTTPException(429, f"The service limit of {_RUNS_PER_HOUR} pre-screens an hour has been reached. "
                                 "Try again later.")
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
        "thresholds": THRESHOLDS,
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
    save_report(report, meta)
    payload = report.model_dump(mode="json")
    payload["meta"] = meta
    return payload


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
        save_report(report, meta)
        payload = report.model_dump(mode="json")
        payload["meta"] = meta
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
    return list_recent()


@router.get("/{report_id}")
async def get_report(report_id: str) -> dict:
    payload = load_report(report_id)
    if payload is None:
        raise HTTPException(404, "Report not found.")
    return payload


@router.post("/{report_id}/decision")
async def decision(report_id: str, req: DecisionRequest) -> dict:
    entry = record_decision(report_id, req)
    if entry is None:
        raise HTTPException(404, "Report not found.")
    return entry
