"""
retrievers/company_profile_retriever.py
----------------------------------------
Retrieves Companies House company profile and wraps it in an EvidenceItem.

Responsibility:
  Call CompaniesHouseConnector.fetch_profile() and produce a
  structured EvidenceItem with a human-readable summary.
"""

from __future__ import annotations

from datetime import datetime

from src.connectors.base import ConnectorError
from src.connectors.companies_house import CompaniesHouseConnector
from src.retrievers.base import BaseRetriever
from src.schemas import EvidenceItem, EvidenceQuality, ResearchDimension
from src.utils import safe_get


class CompanyProfileRetriever(BaseRetriever):
    dimension = ResearchDimension.COMPANY_PROFILE

    def __init__(self) -> None:
        super().__init__(CompaniesHouseConnector())

    def retrieve(self, company_number: str, **kwargs: object) -> EvidenceItem:
        try:
            raw = self.connector.fetch_profile(company_number)  # type: ignore[attr-defined]
            status = safe_get(raw, "company_status", default="unknown")
            name = safe_get(raw, "company_name", default="unknown")
            incorporated = safe_get(raw, "date_of_creation", default="unknown")
            summary = (
                f"{name} | Status: {status} | Incorporated: {incorporated} | "
                f"Type: {safe_get(raw, 'company_type', default='unknown')}"
            )
            quality = EvidenceQuality.HIGH if status != "unknown" else EvidenceQuality.LOW
            return EvidenceItem(
                source="Companies House",
                dimension=self.dimension,
                retrieved_at=datetime.utcnow(),
                raw_data=raw,
                summary=summary,
                quality=quality,
                confidence=0.95 if quality == EvidenceQuality.HIGH else 0.4,
            )
        except ConnectorError as e:
            return self._error_evidence(e)
