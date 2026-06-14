"""Retrieve fraud-signal evidence directly from OpenSanctions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.connectors.base import ConnectorError
from src.connectors.open_sanctions import OpenSanctionsConnector
from src.retrievers.base import BaseRetriever
from src.schemas import EvidenceItem, EvidenceQuality, ResearchDimension


class SanctionsRetriever(BaseRetriever):
    """Screen the resolved company through OpenSanctions."""

    dimension = ResearchDimension.FRAUD_SIGNALS

    def __init__(self, connector: OpenSanctionsConnector | None = None) -> None:
        super().__init__(connector or OpenSanctionsConnector())

    def retrieve(
        self,
        company_number: str,
        company_name: str = "",
        **kwargs: object,
    ) -> EvidenceItem:
        try:
            result = self.connector.screen_entities(  # type: ignore[attr-defined]
                company_name=company_name or company_number,
                officer_names=[],
            )
            if result.get("error"):
                return self._error_evidence(ConnectorError(str(result["error"])))

            sanctions_hits = _extract_hits(result)
            hit_count = len(sanctions_hits)
            return EvidenceItem(
                source="OpenSanctions",
                dimension=self.dimension,
                retrieved_at=datetime.utcnow(),
                raw_data={
                    "company_name": company_name,
                    "sanctions_hits": sanctions_hits,
                    "sanctions_screened_count": result.get("screened_count", 0),
                },
                summary=(
                    f"Entities screened: {result.get('screened_count', 0)} | "
                    f"Sanctions hits: {hit_count}"
                ),
                quality=EvidenceQuality.HIGH,
                confidence=0.95 if hit_count else 0.9,
            )
        except ConnectorError as error:
            return self._error_evidence(error)
        except Exception as error:
            return self._error_evidence(
                ConnectorError(f"SanctionsRetriever error: {error}")
            )


def _extract_hits(result: dict[str, Any]) -> list[str]:
    """Convert OpenSanctions matches into stable evidence strings."""
    hits: list[str] = []
    for entity_key, matches in result.get("hits", {}).items():
        for match in matches:
            caption = match.get("caption", "unknown")
            datasets = ", ".join(match.get("datasets", [])[:3])
            score = float(match.get("score", 0.0))
            hits.append(
                f"{caption} | lists: {datasets} | score: {score:.2f} | "
                f"query: {entity_key}"
            )
    return hits
