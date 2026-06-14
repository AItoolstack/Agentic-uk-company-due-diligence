"""Retrieve and deduplicate news candidates for later LLM classification."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from src.connectors.base import ConnectorError
from src.connectors.brave_search import BraveSearchConnector
from src.retrievers.base import BaseRetriever
from src.schemas import (
    EvidenceItem,
    EvidenceQuality,
    NewsCandidate,
    NewsSignalCategory,
    ResearchDimension,
)
from src.tracing import tracer

_CATEGORY_QUERIES: dict[NewsSignalCategory, str] = {
    NewsSignalCategory.REGULATORY: (
        '"{company}" FCA PRA enforcement fine regulatory action notice UK'
    ),
    NewsSignalCategory.FINANCIAL_DISTRESS: (
        '"{company}" insolvency administration CVA profit warning refinancing UK'
    ),
    NewsSignalCategory.LITIGATION: (
        '"{company}" lawsuit court claim legal action tribunal UK'
    ),
    NewsSignalCategory.OPERATIONAL_INCIDENT: (
        '"{company}" data breach cyber attack outage incident failure UK'
    ),
    NewsSignalCategory.GOVERNANCE_CHANGE: (
        '"{company}" CEO CFO director departure resignation board change UK'
    ),
    NewsSignalCategory.FRAUD_ALLEGATION: (
        '"{company}" fraud allegation investigation SFO whistleblower UK'
    ),
    NewsSignalCategory.MA_ACTIVITY: (
        '"{company}" acquisition merger takeover funding round investment UK'
    ),
}

_RESULTS_PER_CATEGORY = 5
_MAX_CANDIDATES = 20


class NewsRetriever(BaseRetriever):
    """Gather source material without making underwriting severity decisions."""

    dimension = ResearchDimension.NEWS_SIGNALS

    def __init__(self, connector: BraveSearchConnector | None = None) -> None:
        super().__init__(connector or BraveSearchConnector())

    def retrieve(
        self,
        company_number: str,
        company_name: str = "",
        **kwargs: object,
    ) -> EvidenceItem:
        company = company_name or company_number
        try:
            if self.connector.use_mock or not _brave_key_present():
                candidates = _load_mock_candidates()
                source_note = "Mock (news_signals.json)"
            else:
                candidates = self._search_all_categories(company)
                source_note = "Brave Search"

            return self._build_evidence_item(candidates, source_note)
        except ConnectorError as error:
            return self._error_evidence(error)
        except Exception as error:
            return self._error_evidence(
                ConnectorError(f"NewsRetriever error: {error}")
            )

    def _search_all_categories(self, company: str) -> list[NewsCandidate]:
        brave = self.connector
        candidates_by_id: dict[str, NewsCandidate] = {}

        def _search_category(
            category: NewsSignalCategory,
        ) -> list[NewsCandidate]:
            query = _CATEGORY_QUERIES[category].format(company=company)
            try:
                raw = brave.search(  # type: ignore[attr-defined]
                    query,
                    count=_RESULTS_PER_CATEGORY,
                    country="GB",
                )
            except ConnectorError as error:
                tracer.log_error("NewsRetriever", error)
                return []
            return _results_to_candidates(raw.get("results", []), category)

        with ThreadPoolExecutor(max_workers=len(_CATEGORY_QUERIES)) as pool:
            futures = {
                pool.submit(_search_category, category): category
                for category in _CATEGORY_QUERIES
            }
            for future in as_completed(futures):
                try:
                    for candidate in future.result():
                        candidates_by_id.setdefault(
                            candidate.candidate_id,
                            candidate,
                        )
                except Exception as error:
                    tracer.log_error("NewsRetriever", error)

        return sorted(
            candidates_by_id.values(),
            key=lambda candidate: candidate.candidate_id,
        )[:_MAX_CANDIDATES]

    def _build_evidence_item(
        self,
        candidates: list[NewsCandidate],
        source_note: str,
    ) -> EvidenceItem:
        count = len(candidates)
        return EvidenceItem(
            source=f"Brave Search news candidates [{source_note}]",
            dimension=self.dimension,
            retrieved_at=datetime.utcnow(),
            raw_data={
                "candidates": [
                    candidate.model_dump(mode="json")
                    for candidate in candidates
                ],
                "signals": [],
                "total_candidates": count,
                "classification_status": "pending" if count else "no_candidates",
                "source": source_note,
            },
            summary=(
                f"Retrieved {count} news candidates awaiting classification."
                if count
                else "No news candidates found."
            ),
            quality=EvidenceQuality.MEDIUM if count else EvidenceQuality.LOW,
            confidence=0.7 if count else 0.3,
        )


def _brave_key_present() -> bool:
    from src.config import settings

    return bool(settings.brave_search_api_key)


def _candidate_id(headline: str, source_url: str) -> str:
    identity = f"{source_url.strip()}|{headline.strip()}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:16]


def _results_to_candidates(
    results: list[dict[str, Any]],
    search_category: NewsSignalCategory,
) -> list[NewsCandidate]:
    candidates: list[NewsCandidate] = []
    for result in results:
        headline = str(result.get("title", "")).strip()
        if not headline:
            continue
        source_url = str(result.get("url", "")).strip()
        description = str(result.get("description", "")).strip()
        candidates.append(
            NewsCandidate(
                candidate_id=_candidate_id(headline, source_url),
                headline=headline,
                source_url=source_url,
                summary=description[:300],
                search_category=search_category,
            )
        )
    return candidates


def _load_mock_candidates() -> list[NewsCandidate]:
    mock_path = (
        Path(__file__).parent.parent.parent
        / "data"
        / "mock_sources"
        / "news_signals.json"
    )
    with mock_path.open(encoding="utf-8") as handle:
        data = json.load(handle)

    candidates: list[NewsCandidate] = []
    for item in data.get("signals", []):
        try:
            headline = str(item.get("headline", "")).strip()
            source_url = str(item.get("source_url", "")).strip()
            if not headline:
                continue
            candidates.append(
                NewsCandidate(
                    candidate_id=_candidate_id(headline, source_url),
                    headline=headline,
                    source_url=source_url,
                    date=str(item.get("date", "")),
                    summary=str(item.get("summary", ""))[:300],
                    search_category=NewsSignalCategory(item["category"]),
                )
            )
        except (KeyError, ValueError) as error:
            tracer.log_error("NewsRetriever", error)
            continue
    return candidates[:_MAX_CANDIDATES]
