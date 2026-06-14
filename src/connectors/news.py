"""
connectors/news.py
--
Connector for the News API (newsapi.org).

Endpoints covered:
  - GET /v2/everything  -> news articles by keyword query

Real API: https://newsapi.org/v2
Auth: apiKey query parameter.

When NEWS_API_KEY is not configured the connector returns a graceful
stub with an empty articles list rather than making a request that
would 401. Set NEWS_API_KEY in .env to enable real news data.

Mock fallback file: data/mock_sources/news.json
"""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.connectors.base import BaseConnector


class NewsConnector(BaseConnector):

    def fetch_articles(
        self,
        query: str,
        language: str = "en",
        page_size: int = 10,
        sort_by: str = "relevancy",
    ) -> dict[str, Any]:
        """Fetch news articles matching a query string.

        Falls back to mock data when use_mock is True.
        Returns a zero-article stub when NEWS_API_KEY is not set,
        to avoid a 401 in real mode.
        """
        if self.use_mock:
            return self._load_mock("news.json")

        if not settings.news_api_key:
            return {
                "status": "skipped",
                "totalResults": 0,
                "articles": [],
                "note": "NEWS_API_KEY not configured -- add it to .env to enable real news.",
            }

        url = f"{settings.news_api_base_url}/everything"
        return self._get(
            url,
            params={
                "q": query,
                "language": language,
                "pageSize": page_size,
                "sortBy": sort_by,
                "apiKey": settings.news_api_key,
            },
        )
