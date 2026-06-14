"""
retrievers/filing_history_retriever.py
---------------------------------------
Retrieves Companies House filing history and wraps it in an EvidenceItem.

Responsibility:
  Call CompaniesHouseConnector.fetch_filing_history() and produce a summary
  of recent filings, highlighting any unusual or overdue patterns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.connectors.base import ConnectorError
from src.connectors.companies_house import CompaniesHouseConnector
from src.retrievers.base import BaseRetriever
from src.schemas import EvidenceItem, EvidenceQuality, ResearchDimension
from src.utils import safe_get


class FilingHistoryRetriever(BaseRetriever):
    dimension = ResearchDimension.FILING_HISTORY

    def __init__(self) -> None:
        super().__init__(CompaniesHouseConnector())

    def retrieve(self, company_number: str, **kwargs: object) -> EvidenceItem:
        try:
            raw = self.connector.fetch_filing_history(company_number)  # type: ignore[attr-defined]
            items: list[dict[str, Any]] = safe_get(raw, "items", default=[])
            total = safe_get(raw, "total_count", default=len(items))
            recent_types = [i.get("type", "") for i in items[:5]]
            summary = (
                f"Total filings: {total} | "
                f"Most recent 5 types: {', '.join(recent_types)} | "
                f"Latest date: {items[0].get('date','unknown') if items else 'N/A'}"
            )
            return EvidenceItem(
                source="Companies House",
                dimension=self.dimension,
                retrieved_at=datetime.utcnow(),
                raw_data=raw,
                summary=summary,
                quality=EvidenceQuality.HIGH if items else EvidenceQuality.MISSING,
                confidence=0.9 if items else 0.1,
            )
        except ConnectorError as e:
            return self._error_evidence(e)
