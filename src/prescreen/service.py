"""
Wires real sources (or the offline demo cases) into the engine, and stores
reports and underwriter decisions as JSON files.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import httpx

from src.config import settings
from src.prescreen import demo_cases
from src.prescreen.engine import Services, run_prescreen
from src.prescreen.monitor import diff, snapshot
from src.prescreen.store import _writable_dir, get_store
from src.prescreen.llm import JSONChat
from src.prescreen.models import DecisionRequest, PreScreenReport, PreScreenRequest, utcnow
from src.prescreen.sources.companies_house import CompaniesHouse, FixtureTransport, LiveTransport
from src.prescreen.sources.fca import FCARegister
from src.prescreen.sources.gazette import Gazette
from src.prescreen.sources.news import NewsSearch
from src.prescreen.sources.sanctions import load_list, parse_csv

CACHE = _writable_dir() / "cache"


class DemoTransport(FixtureTransport):
    """Fixture transport that also answers name search across the demo cases."""

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/search/companies":
            q = re.sub(r"\s+", " ", (params or {}).get("q", "")).strip().lower()
            items = []
            for number, case in demo_cases.cases().items():
                prof = case["ch"].get(f"/company/{number}", {})
                name = prof.get("company_name", "")
                if q and (q in name.lower() or q == number.lower()):
                    addr = prof.get("registered_office_address") or {}
                    items.append({"company_number": number, "title": name,
                                  "company_status": prof.get("company_status", ""),
                                  "date_of_creation": prof.get("date_of_creation", ""),
                                  "address_snippet": ", ".join(v for v in (addr.get("address_line_1"),
                                                                          addr.get("locality")) if v)})
            return {"items": items}
        return await super().get(path, params)


def demo_mode() -> bool:
    return settings.use_mock_data


def build_services(client: httpx.AsyncClient, company_number: str = "",
                   demo_day: int = 1) -> tuple[Services, dict[str, Any]]:
    """Return services plus metadata shown on the report ("data": "live" | "demo").
    demo_day=2 applies the demo cases' "next day" changes, so monitoring can be shown offline."""
    if demo_mode():
        all_cases = demo_cases.cases(day=demo_day)
        ch_data: dict[str, Any] = {}
        for case in all_cases.values():
            ch_data.update({k.lower() if "?q=" in k else k: v for k, v in case["ch"].items()})
        case = all_cases.get(company_number.upper(), {})
        fca_fx = {k.lower() if "?q=" in k else k: v for k, v in (case.get("fca") or {}).items()}
        services = Services(
            ch=CompaniesHouse(DemoTransport(ch_data)),
            fca=FCARegister(None, "", "", "", fixtures=fca_fx),
            news=NewsSearch(None, fixtures=case.get("news", [])),
            gazette=Gazette(None, fixtures=case.get("gazette", [])),
            llm=_llm(client),
            sanctions=parse_csv(demo_cases.FICTIONAL_SANCTIONS_CSV, "18 Oct 2026 (fictional demo list)",
                                "demo"),
            today=demo_cases.TODAY,
        )
        return services, {"data": "demo", "case_kind": case.get("kind", ""), "case_label": case.get("label", "")}

    services = Services(
        ch=CompaniesHouse(LiveTransport(client, settings.companies_house_api_key,
                                        settings.companies_house_base_url)),
        fca=FCARegister(client, settings.fca_api_key, settings.fca_api_email, settings.fca_base_url),
        news=NewsSearch(client, brave_key=settings.brave_search_api_key),
        gazette=Gazette(client),
        llm=_llm(client),
        sanctions=lambda: load_list(client, CACHE),
        today=date.today(),
    )
    return services, {"data": "live"}


def _llm(client: httpx.AsyncClient) -> JSONChat:
    provider = settings.chat_model_provider
    key = settings.groq_api_key if provider == "groq" else settings.openai_api_key
    model = settings.chat_model_fast or settings.chat_model
    return JSONChat(client, provider, model, key)


# ---------------------------------------------------------------------------
# Storage (see store.py: Postgres when SUPABASE_DB_URL is set, else JSON files)
# ---------------------------------------------------------------------------

async def save_report(report: PreScreenReport, meta: dict[str, Any]) -> dict[str, Any]:
    payload = report.model_dump(mode="json")
    payload["meta"] = meta
    await get_store().save_report(payload)
    return payload


async def load_report(report_id: str) -> dict[str, Any] | None:
    return await get_store().load_report(report_id)


async def record_decision(report_id: str, req: DecisionRequest) -> dict[str, Any] | None:
    """Append-only: a later decision is added beside the earlier ones, never over them."""
    payload = await get_store().load_report(report_id)
    if payload is None:
        return None
    entry = {
        **req.model_dump(),
        "recorded_at": utcnow().isoformat(),
        "recommended_route": payload.get("route"),
        "rules_version": payload.get("rules_version"),
    }
    return await get_store().add_decision(report_id, entry)


async def list_recent(limit: int = 12) -> list[dict[str, Any]]:
    return await get_store().recent(limit)


# ---------------------------------------------------------------------------
# Watching and the daily re-check
# ---------------------------------------------------------------------------

async def watch_from_report(report_id: str) -> dict[str, Any] | None:
    payload = await get_store().load_report(report_id)
    if payload is None:
        return None
    snap = snapshot(payload)
    await get_store().watch(payload["company_number"], payload["company_name"], report_id, snap, payload["route"])
    return await get_store().get_watch(payload["company_number"])


async def recheck_all(client: httpx.AsyncClient, limit: int = 100) -> dict[str, Any]:
    """Re-run the rule-based checks for every watched company and record what changed."""
    store = get_store()
    watched = (await store.list_watch())[:limit]
    summary = {"checked": 0, "changed": 0, "failed": [], "changes": 0, "purged": 0}
    for w in watched:
        number = w["company_number"]
        old = w.get("snapshot") or {}
        try:
            req = PreScreenRequest(**{**(old.get("request") or {}), "company_number": number})
            services, meta = build_services(client, number, demo_day=2)
            report = await run_prescreen(req, services, skip_media=True)
        except Exception as exc:  # noqa: BLE001 - one company failing mustn't stop the rest
            summary["failed"].append({"company_number": number, "error": type(exc).__name__})
            continue
        meta["source"] = "daily re-check"
        payload = await save_report(report, meta)
        new = snapshot(payload)
        changes = [{**c, "report_id": report.report_id} for c in diff(old, new)]
        if changes:
            await store.add_changes(number, changes)
            summary["changed"] += 1
            summary["changes"] += len(changes)
        await store.update_watch(number, new, report.route.value)
        summary["checked"] += 1
    summary["purged"] = await store.purge()
    return summary
