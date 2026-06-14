"""
retrievers/base.py
------------------
Abstract base class for all typed retrievers.

Retrievers sit between connectors (raw data) and agents (business logic).
They call a connector and return a validated Pydantic EvidenceItem.

Subclass responsibilities:
  - Accept a connector in __init__.
  - Implement retrieve() returning EvidenceItem.
  - Handle connector errors and return a low-confidence EvidenceItem on failure.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from src.connectors.base import BaseConnector, ConnectorError
from src.schemas import EvidenceItem, EvidenceQuality, ResearchDimension
from src.tracing import tracer


class BaseRetriever(ABC):
    """Base class for all typed retrievers."""

    dimension: ResearchDimension  # set by subclass

    def __init__(self, connector: BaseConnector) -> None:
        self.connector = connector

    @abstractmethod
    def retrieve(self, company_number: str, **kwargs: object) -> EvidenceItem:
        """Fetch data and return a validated EvidenceItem."""
        ...

    def _error_evidence(self, error: Exception) -> EvidenceItem:
        """Return a placeholder EvidenceItem when retrieval fails."""
        tracer.log_error(self.__class__.__name__, error)
        return EvidenceItem(
            source=self.__class__.__name__,
            dimension=self.dimension,
            retrieved_at=datetime.utcnow(),
            raw_data={},
            summary=f"Retrieval failed: {error}",
            quality=EvidenceQuality.MISSING,
            confidence=0.0,
        )
