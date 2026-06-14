"""Supported source routes and their retriever implementations."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from src.retrievers.base import BaseRetriever
from src.retrievers.beneficial_ownership_retriever import BeneficialOwnershipRetriever
from src.retrievers.company_profile_retriever import CompanyProfileRetriever
from src.retrievers.fca_retriever import FCARetriever
from src.retrievers.filing_history_retriever import FilingHistoryRetriever
from src.retrievers.fraud_retriever import FraudRetriever
from src.retrievers.news_retriever import NewsRetriever
from src.retrievers.officers_retriever import OfficersRetriever
from src.retrievers.sanctions_retriever import SanctionsRetriever
from src.retrievers.web_evidence_retriever import WebEvidenceRetriever
from src.schemas import ConnectorName, ResearchDimension

RetrieverFactory = Callable[[], BaseRetriever]

_ROUTES: dict[tuple[ResearchDimension, ConnectorName], RetrieverFactory] = {
    (ResearchDimension.COMPANY_PROFILE, ConnectorName.COMPANIES_HOUSE):
        CompanyProfileRetriever,
    (ResearchDimension.OFFICERS, ConnectorName.COMPANIES_HOUSE):
        OfficersRetriever,
    (ResearchDimension.FILING_HISTORY, ConnectorName.COMPANIES_HOUSE):
        FilingHistoryRetriever,
    (ResearchDimension.REGULATORY_STATUS, ConnectorName.FCA_REGISTER):
        FCARetriever,
    (ResearchDimension.NEWS_SIGNALS, ConnectorName.BRAVE_SEARCH):
        NewsRetriever,
    (ResearchDimension.WEB_EVIDENCE, ConnectorName.WEB_EVIDENCE):
        WebEvidenceRetriever,
    (ResearchDimension.FRAUD_SIGNALS, ConnectorName.COMPANIES_HOUSE_FRAUD):
        FraudRetriever,
    (ResearchDimension.FRAUD_SIGNALS, ConnectorName.OPEN_SANCTIONS):
        SanctionsRetriever,
    (ResearchDimension.BENEFICIAL_OWNERSHIP, ConnectorName.COMPANIES_HOUSE_PSC):
        BeneficialOwnershipRetriever,
}

_DEFAULT_CONNECTOR: dict[ResearchDimension, ConnectorName] = {
    ResearchDimension.COMPANY_PROFILE: ConnectorName.COMPANIES_HOUSE,
    ResearchDimension.OFFICERS: ConnectorName.COMPANIES_HOUSE,
    ResearchDimension.FILING_HISTORY: ConnectorName.COMPANIES_HOUSE,
    ResearchDimension.REGULATORY_STATUS: ConnectorName.FCA_REGISTER,
    ResearchDimension.NEWS_SIGNALS: ConnectorName.BRAVE_SEARCH,
    ResearchDimension.WEB_EVIDENCE: ConnectorName.WEB_EVIDENCE,
    ResearchDimension.FRAUD_SIGNALS: ConnectorName.COMPANIES_HOUSE_FRAUD,
    ResearchDimension.BENEFICIAL_OWNERSHIP: ConnectorName.COMPANIES_HOUSE_PSC,
}

_EXECUTION_FOOTPRINT: dict[
    tuple[ResearchDimension, ConnectorName],
    tuple[ConnectorName, ...],
] = {
    route: (route[1],) for route in _ROUTES
}
_EXECUTION_FOOTPRINT[
    (ResearchDimension.WEB_EVIDENCE, ConnectorName.WEB_EVIDENCE)
] = (ConnectorName.WEB_EVIDENCE, ConnectorName.BRAVE_SEARCH)
_EXECUTION_FOOTPRINT[
    (ResearchDimension.FRAUD_SIGNALS, ConnectorName.COMPANIES_HOUSE_FRAUD)
] = (
    ConnectorName.COMPANIES_HOUSE_FRAUD,
    ConnectorName.OPEN_SANCTIONS,
)
_EXECUTION_FOOTPRINT[
    (ResearchDimension.BENEFICIAL_OWNERSHIP, ConnectorName.COMPANIES_HOUSE_PSC)
] = (
    ConnectorName.COMPANIES_HOUSE_PSC,
    ConnectorName.OPEN_SANCTIONS,
)


def get_retriever_factory(
    dimension: ResearchDimension,
    connector: ConnectorName,
) -> RetrieverFactory | None:
    """Return the implementation for a validated dimension/source route."""
    return _ROUTES.get((dimension, connector))


def is_supported_route(
    dimension: ResearchDimension,
    connector: ConnectorName,
) -> bool:
    """Return whether the selected connector can execute the dimension."""
    return (dimension, connector) in _ROUTES


def route_footprint(
    dimension: ResearchDimension,
    connector: ConnectorName,
) -> tuple[ConnectorName, ...]:
    """Return the connectors a selected primary source route may invoke."""
    return _EXECUTION_FOOTPRINT.get((dimension, connector), (connector,))


def default_connector_for(dimension: ResearchDimension) -> ConnectorName:
    """Return the fail-safe connector for a research dimension."""
    return _DEFAULT_CONNECTOR[dimension]


def default_source_mapping(
    dimensions: Iterable[ResearchDimension],
) -> dict[ResearchDimension, ConnectorName]:
    """Build the fail-safe source map for the requested dimensions."""
    return {dimension: default_connector_for(dimension) for dimension in dimensions}
