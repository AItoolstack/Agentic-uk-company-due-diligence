"""
API for the underwriter pre-screen.

  GET  /api/prescreen/config            what's connected, demo or live, rules version
  GET  /api/prescreen/search?q=         resolve a name or number (identity gate)
  POST /api/prescreen                   run a pre-screen for one company number
  GET  /api/prescreen/{report_id}       fetch a saved report
  POST /api/prescreen/{report_id}/decision   record the underwriter's decision and reason
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

import httpx
from fastapi import APIRouter, HTTPException, Request

from src.config import settings
from src.prescreen import demo_cases
from src.prescreen.engine import run_prescreen
from src.prescreen.identity import resolve
from src.prescreen.models import DecisionRequest, IdentityResult, PreScreenRequest
from src.prescreen.rules import RULES_VERSION, THRESHOLDS
from src.prescreen.service import build_services, demo_mode, load_report, record_decision, save_report

router = APIRouter(prefix="/api/prescreen", tags=["prescreen"])

# Simple per-client limit so a shared demo can't burn the API quotas.
_RUNS_PER_HOUR = 12
_recent: dict[str, deque] = defaultdict(deque)


def _limit(request: Request) -> None:
    if demo_mode():
        return
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0]
    now = time.time()
    q = _recent[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= _RUNS_PER_HOUR:
        raise HTTPException(429, f"Limit of {_RUNS_PER_HOUR} pre-screens an hour reached. Try again later.")
    q.append(now)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, headers={"User-Agent": "ai-toolstack-prescreen/1.0"})


@router.get("/config")
async def config() -> dict:
    live = not demo_mode()
    return {
        "data": "live" if live else "demo",
        "rules_version": RULES_VERSION,
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
async def prescreen(req: PreScreenRequest, request: Request) -> dict:
    _limit(request)
    async with _client() as client:
        services, meta = build_services(client, req.company_number)
        try:
            report = await run_prescreen(req, services)
        except ValueError as err:
            raise HTTPException(404, str(err)) from err
    meta["news_queries"] = services.news.queries
    save_report(report, meta)
    payload = report.model_dump(mode="json")
    payload["meta"] = meta
    return payload


@router.get("/{report_id}")
async def get_report(report_id: str) -> dict:
    payload = load_report(report_id)
    if payload is None:
        raise HTTPException(404, "Report not found.")
    return payload


@router.post("/{report_id}/decision")
async def decision(report_id: str, req: DecisionRequest) -> dict:
    payload = record_decision(report_id, req)
    if payload is None:
        raise HTTPException(404, "Report not found.")
    return payload["decision"]
