"""
retrievers/web_evidence_retriever.py
--------------------------------------
Retrieves open-web evidence via the Brave Search API and wraps it
in an EvidenceItem.

Uses WebEvidenceConnector (which delegates to BraveSearchConnector).
Falls back gracefully when no API key is configured.
"""

from __future__ import annotations

from datetime import datetime

from src.connectors.base import ConnectorError
from src.connectors.web_evidence import WebEvidenceConnector
from src.retrievers.base import BaseRetriever
from src.schemas import EvidenceItem, EvidenceQuality, ResearchDimension


class WebEvidenceRetriever(BaseRetriever):
    dimension = ResearchDimension.WEB_EVIDENCE

    def __init__(self) -> None:
        super().__init__(WebEvidenceConnector())

    def retrieve(self, company_number: str, company_name: str = "", **kwargs: object) -> EvidenceItem:  # type: ignore[override]
        query = f"{company_name or company_number} UK regulatory news risk"
        try:
            raw = self.connector.search(query, num_results=5)  # type: ignore[attr-defined]
            results = raw.get("results", [])
            note = raw.get("note", "")

            if note:
                # API not configured -- return a low-confidence stub
                return EvidenceItem(
                    source="Brave Search (not configured)",
                    dimension=self.dimension,
                    retrieved_at=datetime.utcnow(),
                    raw_data=raw,
                    summary=note,
                    quality=EvidenceQuality.MISSING,
                    confidence=0.0,
                )

            snippets = "; ".join(
                f"{r['title']}: {r['description'][:80]}"
                for r in results[:3]
                if r.get("title")
            )
            summary = (
                f"Web results: {len(results)} | Top: {snippets}"
                if snippets
                else "No web results found."
            )
            quality = EvidenceQuality.MEDIUM if results else EvidenceQuality.LOW
            return EvidenceItem(
                source="Brave Search",
                dimension=self.dimension,
                retrieved_at=datetime.utcnow(),
                raw_data=raw,
                summary=summary,
                quality=quality,
                confidence=0.6 if results else 0.2,
            )
        except ConnectorError as e:
            return self._error_evidence(e)
