"""
retrievers/officers_retriever.py
---------------------------------
Retrieves Companies House officer list and wraps it in an EvidenceItem.

Responsibility:
  Call CompaniesHouseConnector.fetch_officers() and produce a summary
  of active and resigned officers for use by the OfficersAgent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.connectors.base import ConnectorError
from src.connectors.companies_house import CompaniesHouseConnector
from src.retrievers.base import BaseRetriever
from src.schemas import EvidenceItem, EvidenceQuality, ResearchDimension
from src.utils import safe_get


class OfficersRetriever(BaseRetriever):
    dimension = ResearchDimension.OFFICERS

    def __init__(self) -> None:
        super().__init__(CompaniesHouseConnector())

    def retrieve(self, company_number: str, **kwargs: object) -> EvidenceItem:
        try:
            raw = self.connector.fetch_officers(company_number)  # type: ignore[attr-defined]
            items: list[dict[str, Any]] = safe_get(raw, "items", default=[])
            active = [o for o in items if not o.get("resigned_on")]
            resigned = [o for o in items if o.get("resigned_on")]
            summary = (
                f"Active officers: {len(active)} | Resigned: {len(resigned)} | "
                f"Roles: {', '.join(set(o.get('officer_role','') for o in active))}"
            )
            return EvidenceItem(
                source="Companies House",
                dimension=self.dimension,
                retrieved_at=datetime.utcnow(),
                raw_data=raw,
                summary=summary,
                quality=EvidenceQuality.HIGH if active else EvidenceQuality.MEDIUM,
                confidence=0.9,
            )
        except ConnectorError as e:
            return self._error_evidence(e)
