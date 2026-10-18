"""
Wires real sources (or the offline demo cases) into the engine, and stores
reports and underwriter decisions as JSON files.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from src.config import settings
from src.prescreen import demo_cases
from src.prescreen.engine import Services
from src.prescreen.llm import JSONChat
from src.prescreen.models import DecisionRequest, PreScreenReport, utcnow
from src.prescreen.sources.companies_house import CompaniesHouse, FixtureTransport, LiveTransport
from src.prescreen.sources.fca import FCARegister
from src.prescreen.sources.gazette import Gazette
from src.prescreen.sources.news import NewsSearch
from src.prescreen.sources.sanctions import load_list, parse_csv

ROOT = Path(__file__).resolve().parents[2]


def _writable_dir() -> Path:
    """output/ next to the code if we can write there, else the temp folder (e.g. a read-only container)."""
    preferred = Path(os.environ.get("PRESCREEN_DATA_DIR", ROOT / "output"))
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return preferred
    except OSError:
        return Path(tempfile.gettempdir()) / "prescreen"


_BASE = _writable_dir()
STORE = _BASE / "prescreens"
CACHE = _BASE / "cache"


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


def build_services(client: httpx.AsyncClient, company_number: str = "") -> tuple[Services, dict[str, Any]]:
    """Return services plus metadata shown on the report ("data": "live" | "demo")."""
    if demo_mode():
        all_cases = demo_cases.cases()
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
# Storage: one JSON file per report; decisions are appended to the report.
# ---------------------------------------------------------------------------

def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def save_report(report: PreScreenReport, meta: dict[str, Any]) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    payload = report.model_dump(mode="json")
    payload["meta"] = meta
    payload["decisions"] = []
    _atomic_write(STORE / f"{report.report_id}.json", payload)


def load_report(report_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[0-9a-f]{16}", report_id or ""):
        return None
    path = STORE / f"{report_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def record_decision(report_id: str, req: DecisionRequest) -> dict[str, Any] | None:
    """Append-only: a later decision is added beside the earlier ones, never over them."""
    payload = load_report(report_id)
    if payload is None:
        return None
    entry = {
        **req.model_dump(),
        "recorded_at": utcnow().isoformat(),
        "recommended_route": payload.get("route"),
        "rules_version": payload.get("rules_version"),
    }
    payload.setdefault("decisions", []).append(entry)
    payload["decision"] = entry  # latest, for convenience
    _atomic_write(STORE / f"{report_id}.json", payload)
    return entry


def list_recent(limit: int = 12) -> list[dict[str, Any]]:
    """Newest reports first: enough to show a case list, not the reports themselves."""
    if not STORE.exists():
        return []
    files = sorted(STORE.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
    out = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        last = (d.get("decisions") or [None])[-1]
        out.append({
            "report_id": d.get("report_id"), "company_number": d.get("company_number"),
            "company_name": d.get("company_name"), "route": d.get("route"), "created_at": d.get("created_at"),
            "decision": last.get("decision") if last else None,
            "data": (d.get("meta") or {}).get("data", ""),
        })
    return out
