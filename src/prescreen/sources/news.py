"""
News and web search for adverse media.

Brave is the primary provider (the Space already has a key). GDELT is a free,
keyless fallback with roughly three months of history. Only headline, URL,
date, outlet and a short snippet are kept: never page text.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from src.prescreen.sources.http import NOT_FOUND, SourceError, get_json

BRAVE_WEB = "https://api.search.brave.com/res/v1/web/search"
BRAVE_NEWS = "https://api.search.brave.com/res/v1/news/search"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"

ADVERSE_TERMS = (
    "fraud OR investigation OR fined OR prosecuted OR prosecution OR insolvency OR "
    "administration OR liquidation OR \"winding up\" OR lawsuit OR sanctions OR fire OR recall"
)


def short_name(registered_name: str) -> str:
    """'WILKO LIMITED' -> 'Wilko'. Keeps the distinctive part for search."""
    s = re.sub(r"\b(LIMITED|LTD|PLC|LLP|PUBLIC LIMITED COMPANY|COMPANY|CO)\b\.?", "", registered_name, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" .,&-")
    return s.title() if s.isupper() else s


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


class NewsSearch:
    def __init__(self, client: httpx.AsyncClient | None, brave_key: str = "",
                 fixtures: list[dict[str, Any]] | SourceError | None = None) -> None:
        self.client = client
        self.brave_key = brave_key
        self.fixtures = fixtures
        self.provider_used = ""
        self.fallback_reason = ""
        self.queries: list[str] = []

    async def search(self, registered_name: str, limit: int = 12,
                     previous_names: list[str] | None = None) -> list[dict[str, str]]:
        if self.fixtures is not None:
            if isinstance(self.fixtures, SourceError):
                raise self.fixtures
            self.provider_used = "fixtures"
            return list(self.fixtures)[:limit]

        name = short_name(registered_name)
        others = [short_name(p) for p in (previous_names or []) if p][:2]
        if self.brave_key:
            try:
                items = await self._brave(name, others)
                self.provider_used = "Brave Search"
                return _dedupe(items)[:limit]
            except SourceError as err:
                self.fallback_reason = err.plain
        items = await self._gdelt(name)
        self.provider_used = "GDELT (free fallback, ~3 months)" if self.brave_key else "GDELT (free, ~3 months)"
        return _dedupe(items)[:limit]

    async def _brave(self, name: str, others: list[str] | None = None) -> list[dict[str, str]]:
        headers = {"X-Subscription-Token": self.brave_key, "Accept": "application/json"}
        q_news = " OR ".join(f'"{n}"' for n in [name, *(others or [])])
        q_web = f'"{name}" ({ADVERSE_TERMS})'
        self.queries += [f"brave news: {q_news}", f"brave web: {q_web}"]
        news = await get_json(self.client, "Brave Search", BRAVE_NEWS, headers=headers,
                              params={"q": q_news, "count": 10, "country": "GB", "search_lang": "en"})
        web = await get_json(self.client, "Brave Search", BRAVE_WEB, headers=headers,
                             params={"q": q_web, "count": 10, "country": "GB", "search_lang": "en"})
        items: list[dict[str, str]] = []
        for raw in (news, web):
            if raw is NOT_FOUND or not isinstance(raw, dict):
                continue
            results = raw.get("results") or (raw.get("web") or {}).get("results") or []
            for r in results:
                items.append({
                    "title": _strip_tags(r.get("title", "")),
                    "url": r.get("url", ""),
                    "published": r.get("page_age") or r.get("age") or "",
                    "source": (r.get("meta_url") or {}).get("hostname", ""),
                    "snippet": _strip_tags(r.get("description", ""))[:300],
                })
        return items

    async def _gdelt(self, name: str) -> list[dict[str, str]]:
        q = f'"{name}" sourcecountry:UK'
        self.queries.append(f"gdelt: {q}")
        raw = await get_json(self.client, "GDELT", GDELT,
                             params={"query": q, "mode": "artlist", "format": "json",
                                     "maxrecords": 20, "timespan": "3months", "sort": "datedesc"})
        if raw is NOT_FOUND or not isinstance(raw, dict):
            return []
        return [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "published": a.get("seendate", ""),
                "source": a.get("domain", ""),
                "snippet": "",
            }
            for a in raw.get("articles") or []
        ]


def _dedupe(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen, out = set(), []
    for it in items:
        key = it.get("url", "").split("?")[0].rstrip("/")
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out
