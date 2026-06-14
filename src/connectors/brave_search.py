"""
connectors/brave_search.py
---------------------------
Connector for the Brave Search API.

Endpoint:
  GET https://api.search.brave.com/res/v1/web/search

Auth: X-Subscription-Token header (free tier: 2,000 requests/month).
Docs: https://api.search.brave.com/app/documentation/web-search

Mock fallback: returns a stub with empty results list.
"""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.connectors.base import BaseConnector, ConnectorError

_BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveSearchConnector(BaseConnector):

    def search(
        self,
        query: str,
        count: int = 5,
        country: str = "GB",
    ) -> dict[str, Any]:
        """Search the open web via Brave Search.

        Args:
            query:   Search query string.
            count:   Number of results (max 20 on free tier).
            country: Two-letter country code to bias results.

        Returns:
            Dict with "results" key containing a list of
            {"title", "url", "description"} dicts.
        """
        if self.use_mock or not settings.brave_search_api_key:
            return {
                "query": query,
                "results": [],
                "note": "Brave Search not configured -- mock stub returned.",
            }
        try:
            raw = self._get(
                _BRAVE_API_URL,
                params={"q": query, "count": count, "country": country},
                headers={
                    "X-Subscription-Token": settings.brave_search_api_key,
                    "Accept": "application/json",
                },
            )
            web = raw.get("web", {})
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "description": r.get("description", ""),
                }
                for r in web.get("results", [])
            ]
            return {"query": query, "results": results}
        except ConnectorError:
            raise
        except Exception as e:
            raise ConnectorError(f"Brave Search failed for '{query}': {e}") from e
