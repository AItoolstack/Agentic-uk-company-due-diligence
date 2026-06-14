"""
connectors/web_evidence.py
Web evidence gathering via Brave Search API.

Delegates to BraveSearchConnector for real API calls.
Falls back gracefully when BRAVE_SEARCH_API_KEY is not set.
"""

from __future__ import annotations

from typing import Any

from src.connectors.base import BaseConnector
from src.connectors.brave_search import BraveSearchConnector


class WebEvidenceConnector(BaseConnector):

    def __init__(self) -> None:
        super().__init__()
        self._brave = BraveSearchConnector()

    def search(self, query: str, num_results: int = 5) -> dict[str, Any]:
        """Search the open web and return result snippets."""
        return self._brave.search(query, count=num_results)

    def fetch_page(self, url: str) -> dict[str, Any]:
        """Fetch and extract text content from a specific URL.

        Uses a lightweight GET via the base session.
        Returns plain text content (first 4000 chars to stay within token limits).
        """
        if self.use_mock:
            return {"url": url, "content": "", "note": "Mock stub."}
        try:
            response = self._session.get(url, timeout=10)
            response.raise_for_status()
            text = response.text[:4000]
            return {"url": url, "content": text}
        except Exception as e:
            return {"url": url, "content": "", "error": str(e)}
