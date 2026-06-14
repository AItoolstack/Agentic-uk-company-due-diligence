"""Tests for validated and operational source selection."""

from __future__ import annotations

import asyncio
from datetime import datetime

from src.agents.source_selector import normalise_source_selection
from src.schemas import (
    ConnectorName,
    EntityResolutionOutput,
    EvidenceItem,
    EvidenceQuality,
    ResearchDimension,
    ResearchPlanOutput,
    SourceSelectionOutput,
)
from src.source_registry import route_footprint


def test_normalise_preserves_valid_choice_and_repairs_incompatible_route() -> None:
    selection = SourceSelectionOutput(
        dimension_to_connector={
            ResearchDimension.FRAUD_SIGNALS: ConnectorName.OPEN_SANCTIONS,
            ResearchDimension.COMPANY_PROFILE: ConnectorName.OPEN_SANCTIONS,
        },
        rationale="Prioritise direct sanctions screening.",
        confidence=0.9,
    )

    normalised = normalise_source_selection(
        selection,
        [
            ResearchDimension.FRAUD_SIGNALS,
            ResearchDimension.COMPANY_PROFILE,
            ResearchDimension.OFFICERS,
        ],
    )

    assert normalised.dimension_to_connector == {
        ResearchDimension.FRAUD_SIGNALS: ConnectorName.OPEN_SANCTIONS,
        ResearchDimension.COMPANY_PROFILE: ConnectorName.COMPANIES_HOUSE,
        ResearchDimension.OFFICERS: ConnectorName.COMPANIES_HOUSE,
    }
    assert normalised.confidence == 0.5
    assert "Compatibility fallbacks" in normalised.rationale


def test_route_footprint_discloses_enrichment_connectors() -> None:
    assert route_footprint(
        ResearchDimension.BENEFICIAL_OWNERSHIP,
        ConnectorName.COMPANIES_HOUSE_PSC,
    ) == (
        ConnectorName.COMPANIES_HOUSE_PSC,
        ConnectorName.OPEN_SANCTIONS,
    )


def test_source_selector_agent_preserves_valid_llm_choice(monkeypatch) -> None:
    from src.agents import source_selector as selector_module

    selected = SourceSelectionOutput(
        dimension_to_connector={
            ResearchDimension.FRAUD_SIGNALS: ConnectorName.OPEN_SANCTIONS,
        },
        rationale="Sanctions screening is the primary concern.",
        confidence=0.92,
    )

    class FakeStructuredLLM:
        def invoke(self, messages):
            return selected

    class FakeLLM:
        def with_structured_output(self, schema):
            assert schema is SourceSelectionOutput
            return FakeStructuredLLM()

    monkeypatch.setattr(selector_module, "get_llm", lambda tier: FakeLLM())

    result = selector_module.source_selector_agent(
        {
            "research_plan": ResearchPlanOutput(
                dimensions_to_investigate=[ResearchDimension.FRAUD_SIGNALS],
                rationale="Investigate sanctions exposure.",
                confidence=0.9,
            ),
            "entity_resolution": EntityResolutionOutput(
                company_name="EXAMPLE LIMITED",
                company_number="01234567",
                confidence=0.95,
            ),
        }
    )

    assert result["source_selection"] == selected


def test_collector_executes_the_selected_source(monkeypatch) -> None:
    from src.agents import parallel_source_collector as collector_module

    selected_routes: list[tuple[ResearchDimension, ConnectorName]] = []

    class FakeRetriever:
        def retrieve(
            self,
            company_number: str,
            company_name: str = "",
            firm_reference: str = "",
        ) -> EvidenceItem:
            return EvidenceItem(
                source="selected-open-sanctions",
                dimension=ResearchDimension.FRAUD_SIGNALS,
                retrieved_at=datetime(2026, 6, 14, 10, 0),
                summary=company_name,
                quality=EvidenceQuality.HIGH,
                confidence=0.9,
            )

    def fake_factory_lookup(
        dimension: ResearchDimension,
        connector: ConnectorName,
    ):
        selected_routes.append((dimension, connector))
        return FakeRetriever

    monkeypatch.setattr(
        collector_module,
        "get_retriever_factory",
        fake_factory_lookup,
    )

    result = asyncio.run(
        collector_module.parallel_source_collector(
            {
                "entity_resolution": EntityResolutionOutput(
                    company_name="EXAMPLE LIMITED",
                    company_number="01234567",
                    confidence=0.95,
                ),
                "source_selection": SourceSelectionOutput(
                    dimension_to_connector={
                        ResearchDimension.FRAUD_SIGNALS:
                            ConnectorName.OPEN_SANCTIONS
                    },
                    rationale="Direct sanctions screening",
                    confidence=0.95,
                ),
                "evidence_by_dimension": {},
            }
        )
    )

    assert selected_routes == [
        (ResearchDimension.FRAUD_SIGNALS, ConnectorName.OPEN_SANCTIONS)
    ]
    assert result["source_footprint_this_pass"] == {
        ResearchDimension.FRAUD_SIGNALS.value: [
            ConnectorName.OPEN_SANCTIONS.value
        ]
    }
    assert (
        result["evidence_by_dimension"][
            ResearchDimension.FRAUD_SIGNALS.value
        ].source
        == "selected-open-sanctions"
    )


def test_direct_sanctions_retriever_uses_mock_connector() -> None:
    from src.connectors.open_sanctions import OpenSanctionsConnector
    from src.retrievers.sanctions_retriever import SanctionsRetriever

    connector = OpenSanctionsConnector()
    connector.use_mock = True
    evidence = SanctionsRetriever(connector).retrieve(
        company_number="01234567",
        company_name="EXAMPLE LIMITED",
    )

    assert evidence.source == "OpenSanctions"
    assert evidence.dimension == ResearchDimension.FRAUD_SIGNALS
    assert evidence.raw_data["sanctions_screened_count"] == 1
    assert evidence.quality == EvidenceQuality.HIGH


def test_source_selector_progress_reports_selected_routes() -> None:
    from src.api.routes import _extract_node_detail

    detail = _extract_node_detail(
        "source_selector",
        {
            "source_selection": SourceSelectionOutput(
                dimension_to_connector={
                    ResearchDimension.COMPANY_PROFILE:
                        ConnectorName.COMPANIES_HOUSE,
                    ResearchDimension.FRAUD_SIGNALS:
                        ConnectorName.OPEN_SANCTIONS,
                },
                rationale="Test routes",
                confidence=0.9,
            )
        },
    )

    assert detail == "2 source routes selected"


def test_collector_progress_reports_actual_connector_footprint() -> None:
    from src.api.routes import _extract_node_detail

    detail = _extract_node_detail(
        "parallel_source_collector",
        {
            "collected_dimensions_this_pass": ["fraud_signals"],
            "source_footprint_this_pass": {
                "fraud_signals": [
                    "companies_house_fraud",
                    "open_sanctions",
                ]
            },
        },
    )

    assert detail == (
        "Collected 1 dimension in parallel via "
        "companies_house_fraud, open_sanctions"
    )
