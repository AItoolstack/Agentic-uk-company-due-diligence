"""
retrievers/fca_retriever.py
----------------------------
Retrieves FCA Register firm data and wraps it in an EvidenceItem.

Responsibility:
  Call FCARegisterConnector.fetch_firm() and produce a summary of
  authorisation status, permissions, and any disciplinary history.
"""

from __future__ import annotations

from datetime import datetime

from src.connectors.base import ConnectorError
from src.connectors.fca_register import FCARegisterConnector
from src.retrievers.base import BaseRetriever
from src.schemas import EvidenceItem, EvidenceQuality, ResearchDimension
from src.utils import safe_get


class FCARetriever(BaseRetriever):
    dimension = ResearchDimension.REGULATORY_STATUS

    def __init__(self) -> None:
        super().__init__(FCARegisterConnector())

    def retrieve(self, company_number: str, firm_reference: str = "", **kwargs: object) -> EvidenceItem:  # type: ignore[override]
        try:
            raw = self.connector.fetch_firm(firm_reference or company_number)  # type: ignore[attr-defined]
            status = safe_get(raw, "status", default="unknown")
            frn = safe_get(raw, "firm_reference_number", default="unknown")
            perms = safe_get(raw, "permissions", default=[])
            disciplinary = safe_get(raw, "disciplinary_history", default=[])
            summary = (
                f"FCA Status: {status} | FRN: {frn} | "
                f"Permissions: {len(perms)} | "
                f"Disciplinary actions: {len(disciplinary)}"
            )
            quality = EvidenceQuality.HIGH if status == "Authorised" else EvidenceQuality.MEDIUM
            return EvidenceItem(
                source="FCA Register",
                dimension=self.dimension,
                retrieved_at=datetime.utcnow(),
                raw_data=raw,
                summary=summary,
                quality=quality,
                confidence=0.95 if quality == EvidenceQuality.HIGH else 0.5,
            )
        except ConnectorError as e:
            return self._error_evidence(e)
