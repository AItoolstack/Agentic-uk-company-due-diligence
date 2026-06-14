"""
agents/parallel_source_collector.py
--
ParallelSourceCollector -- replaces the 5 sequential source agent nodes.

Reads:  state["source_selection"], state["entity_resolution"],
        state["evidence_by_dimension"]
Writes: state["evidence_by_dimension"], state["collected_dimensions_this_pass"],
        state["source_footprint_this_pass"]

Runs all selected retrievers concurrently using asyncio.gather +
asyncio.to_thread (retrievers are sync HTTP calls).

Individual failures are converted to MISSING evidence by the retriever or,
as a final boundary, by the collector so one source cannot crash the graph.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from src.evidence_merge import merge_evidence_by_dimension
from src.schemas import ConnectorName, EvidenceItem, EvidenceQuality, ResearchDimension
from src.source_registry import (
    default_source_mapping,
    get_retriever_factory,
    route_footprint,
)
from src.state import AgentState
from src.tracing import tracer


def _run_retriever(
    dimension: ResearchDimension,
    connector: ConnectorName,
    company_number: str,
    company_name: str,
    firm_reference: str,
) -> tuple[str, EvidenceItem]:
    """Run the retriever selected for a dimension/source route."""
    factory = get_retriever_factory(dimension, connector)
    if factory is None:
        tracer.log_error(
            "ParallelSourceCollector",
            ValueError(
                f"Unsupported source route: {dimension.value} -> {connector.value}"
            ),
        )
        return dimension.value, EvidenceItem(
            source=connector.value,
            dimension=dimension,
            retrieved_at=datetime.utcnow(),
            summary=(
                f"No retriever registered for source route "
                f"'{dimension.value} -> {connector.value}'."
            ),
            quality=EvidenceQuality.MISSING,
            confidence=0.0,
        )

    try:
        retriever = factory()
        item = retriever.retrieve(
            company_number=company_number,
            company_name=company_name,
            firm_reference=firm_reference,
        )
    except Exception as error:
        tracer.log_error("ParallelSourceCollector", error)
        item = EvidenceItem(
            source=connector.value,
            dimension=dimension,
            retrieved_at=datetime.utcnow(),
            summary=(
                f"Source route '{dimension.value} -> {connector.value}' "
                f"failed: {error}"
            ),
            quality=EvidenceQuality.MISSING,
            confidence=0.0,
        )
    return dimension.value, item


async def _collect_all(
    assignments: dict[ResearchDimension, ConnectorName],
    company_number: str,
    company_name: str,
    firm_reference: str,
) -> dict[str, EvidenceItem]:
    tasks = [
        asyncio.to_thread(
            _run_retriever,
            dimension,
            connector,
            company_number,
            company_name,
            firm_reference,
        )
        for dimension, connector in assignments.items()
    ]
    results: list[tuple[str, EvidenceItem]] = await asyncio.gather(*tasks)
    return {dim: item for dim, item in results}


async def parallel_source_collector(state: AgentState) -> dict:
    """Async LangGraph node: fan-out all selected sources in parallel."""
    tracer.log_agent_start("ParallelSourceCollector", state)

    er = state.get("entity_resolution")
    company_number = (er.company_number or "") if er else ""
    company_name = er.company_name if er else ""
    firm_reference = (er.fca_firm_reference or "") if er else ""

    sel = state.get("source_selection")
    if sel and sel.dimension_to_connector:
        assignments = sel.dimension_to_connector
    else:
        assignments = default_source_mapping(ResearchDimension)

    tracer.log_event(
        "ParallelSourceCollector",
        "Collecting source routes in parallel: "
        + ", ".join(
            f"{dimension.value}->{connector.value}"
            for dimension, connector in assignments.items()
        ),
    )

    evidence = await _collect_all(
        assignments,
        company_number,
        company_name,
        firm_reference,
    )
    merged_evidence = merge_evidence_by_dimension(
        state.get("evidence_by_dimension", {}),
        evidence,
    )

    tracer.log_agent_end(
        "ParallelSourceCollector",
        {k: v.quality for k, v in evidence.items()},
    )
    return {
        "evidence_by_dimension": merged_evidence,
        "collected_dimensions_this_pass": list(evidence.keys()),
        "source_footprint_this_pass": {
            dimension.value: [
                source.value
                for source in route_footprint(dimension, connector)
            ]
            for dimension, connector in assignments.items()
        },
    }
