"""
Companies House public data API, read-only.

Everything is keyed on the company number. Name search exists only to show the
underwriter candidates to pick from; it never decides the company by itself.

Two transports share one interface:
  - live: the real API (HTTP Basic, key as username)
  - fixtures: a dict of path -> JSON, used for tests and the offline demo
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol

import httpx

from src.prescreen.sources.http import NOT_FOUND, SourceError, get_json

SOURCE = "Companies House"
PUBLIC_SITE = "https://find-and-update.company-information.service.gov.uk"


class Transport(Protocol):
    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


class LiveTransport:
    def __init__(self, client: httpx.AsyncClient, api_key: str, base_url: str) -> None:
        self.client = client
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.gate = asyncio.Semaphore(6)  # a run fans out ~30 calls; keep it polite
        self.calls = 0

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.api_key:
            raise SourceError(SOURCE, "no_key", "COMPANIES_HOUSE_API_KEY not set")
        async with self.gate:
            self.calls += 1
            return await get_json(
                self.client, SOURCE, f"{self.base_url}{path}",
                params=params, auth=(self.api_key, ""),
            )


class FixtureTransport:
    """Path -> JSON. A missing path means 404. A value of `SourceError` is raised."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls: list[str] = []

    @classmethod
    def from_dir(cls, folder: Path) -> "FixtureTransport":
        index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        data = {
            path: json.loads((folder / name).read_text(encoding="utf-8"))
            for path, name in index.items()
        }
        return cls(data)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append(path)
        key = path
        if params and "q" in params:
            key = f"{path}?q={params['q']}".lower()
        value = self.data.get(key, self.data.get(path, NOT_FOUND))
        if isinstance(value, SourceError):
            raise value
        return value


def public_url(company_number: str, suffix: str = "") -> str:
    return f"{PUBLIC_SITE}/company/{company_number}{suffix}"


class CompaniesHouse:
    def __init__(self, transport: Transport) -> None:
        self.t = transport

    async def profile(self, number: str) -> Any:
        return await self.t.get(f"/company/{number}")

    async def search(self, query: str, limit: int = 8) -> Any:
        return await self.t.get("/search/companies", {"q": query, "items_per_page": limit})

    async def officers(self, number: str, max_items: int = 500) -> Any:
        """All officers, paged. Old companies have hundreds (Thames Water: well over 100 counting
        resigned ones), and the first page alone made the director checks 'couldn't check'."""
        path = f"/company/{number}/officers"
        first = await self.t.get(path, {"items_per_page": 100})
        if not isinstance(first, dict):
            return first
        items = list(first.get("items") or [])
        total = int(first.get("total_results") or len(items))
        start = len(items)
        while start < min(total, max_items) and start > 0:
            page = await self.t.get(path, {"items_per_page": 100, "start_index": start})
            if not isinstance(page, dict) or not page.get("items"):
                break
            items += page["items"]
            start += len(page["items"])
        return {**first, "items": items, "total_results": total, "items_per_page": len(items)}

    async def pscs(self, number: str) -> Any:
        return await self.t.get(
            f"/company/{number}/persons-with-significant-control", {"items_per_page": 100}
        )

    async def psc_statements(self, number: str) -> Any:
        return await self.t.get(f"/company/{number}/persons-with-significant-control-statements")

    async def charges(self, number: str) -> Any:
        return await self.t.get(f"/company/{number}/charges", {"items_per_page": 100})

    async def insolvency(self, number: str) -> Any:
        return await self.t.get(f"/company/{number}/insolvency")

    async def appointments(self, officer_id: str, max_items: int = 300) -> Any:
        """All appointments for an officer, paged (the API returns at most 100 a page)."""
        path = f"/officers/{officer_id}/appointments"
        first = await self.t.get(path, {"items_per_page": 100})
        if not isinstance(first, dict):
            return first
        items = list(first.get("items") or [])
        total = int(first.get("total_results") or len(items))
        start = len(items)
        while start < min(total, max_items) and start > 0:
            page = await self.t.get(path, {"items_per_page": 100, "start_index": start})
            if not isinstance(page, dict) or not page.get("items"):
                break
            items += page["items"]
            start += len(page["items"])
        return {**first, "items": items, "total_results": total, "truncated": total > len(items)}

    async def filing_history(self, number: str, category: str) -> Any:
        return await self.t.get(f"/company/{number}/filing-history", {"category": category, "items_per_page": 25})

    async def search_disqualified(self, name: str) -> Any:
        return await self.t.get("/search/disqualified-officers", {"q": name, "items_per_page": 100})

    async def disqualified_natural(self, path: str) -> Any:
        """`path` is the self link from a disqualified-officer search hit."""
        return await self.t.get(path)
