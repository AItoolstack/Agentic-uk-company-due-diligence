"""
Companies House public data API, read-only.

Everything is keyed on the company number. Name search exists only to show the
underwriter candidates to pick from; it never decides the company by itself.

Two transports share one interface:
  - live: the real API (HTTP Basic, key as username)
  - fixtures: a dict of path -> JSON, used for tests and the offline demo
"""

from __future__ import annotations

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

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.api_key:
            raise SourceError(SOURCE, "no_key", "COMPANIES_HOUSE_API_KEY not set")
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

    async def officers(self, number: str) -> Any:
        return await self.t.get(f"/company/{number}/officers", {"items_per_page": 100})

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

    async def appointments(self, officer_id: str) -> Any:
        return await self.t.get(f"/officers/{officer_id}/appointments", {"items_per_page": 100})

    async def search_disqualified(self, name: str) -> Any:
        return await self.t.get("/search/disqualified-officers", {"q": name, "items_per_page": 10})

    async def disqualified_natural(self, path: str) -> Any:
        """`path` is the self link from a disqualified-officer search hit."""
        return await self.t.get(path)
