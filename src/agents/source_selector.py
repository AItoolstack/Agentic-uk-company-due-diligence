"""
agents/source_selector.py
SourceSelectorAgent -- maps research dimensions to data connectors.

Reads:  state["research_plan"], state["entity_resolution"]
Writes: state["source_selection"]
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm import get_llm
from src.prompts import SOURCE_SELECTOR_SYSTEM, SOURCE_SELECTOR_USER
from src.schemas import ConnectorName, ResearchDimension, SourceSelectionOutput
from src.source_registry import (
    default_connector_for,
    default_source_mapping,
    is_supported_route,
)
from src.state import AgentState
from src.tracing import tracer


def source_selector_agent(state: AgentState) -> dict:
    """Map research dimensions to appropriate data connectors."""
    tracer.log_agent_start("SourceSelectorAgent", state)

    plan = state.get("research_plan")
    er = state.get("entity_resolution")

    dimensions = (
        plan.dimensions_to_investigate if plan else list(ResearchDimension)
    )
    company_name = er.company_name if er else "Unknown"

    company_context = "UK bank / FCA-regulated financial institution" \
        if er and er.fca_firm_reference else "UK private limited company"

    llm = get_llm(tier="fast")
    structured = llm.with_structured_output(SourceSelectionOutput)

    messages = [
        SystemMessage(content=SOURCE_SELECTOR_SYSTEM),
        HumanMessage(content=SOURCE_SELECTOR_USER.format(
            company_name=company_name,
            company_context=company_context,
            dimensions=", ".join(d.value for d in dimensions),
        )),
    ]

    try:
        selected: SourceSelectionOutput = structured.invoke(messages)
        output = normalise_source_selection(selected, dimensions)
        tracer.log_agent_end("SourceSelectorAgent", output)
        return {"source_selection": output}
    except Exception as e:
        tracer.log_error("SourceSelectorAgent", e)
        fallback = SourceSelectionOutput(
            dimension_to_connector=default_source_mapping(dimensions),
            rationale="Fallback mapping due to LLM error.",
            confidence=0.5,
        )
        errors = list(state.get("errors", []))
        errors.append({"agent": "source_selector", "error": str(e)})
        return {"source_selection": fallback, "errors": errors}


def normalise_source_selection(
    selection: SourceSelectionOutput,
    dimensions: list[ResearchDimension],
) -> SourceSelectionOutput:
    """Keep valid LLM choices and repair missing or incompatible routes."""
    resolved: dict[ResearchDimension, ConnectorName] = {}
    fallbacks: list[str] = []

    for dimension in dimensions:
        connector = selection.dimension_to_connector.get(dimension)
        if connector is not None and is_supported_route(dimension, connector):
            resolved[dimension] = connector
            continue

        fallback = default_connector_for(dimension)
        resolved[dimension] = fallback
        selected_name = connector.value if connector is not None else "missing"
        fallbacks.append(
            f"{dimension.value}: {selected_name} -> {fallback.value}"
        )

    rationale = selection.rationale
    confidence = selection.confidence
    if fallbacks:
        rationale = (
            f"{rationale} Compatibility fallbacks: {'; '.join(fallbacks)}."
        )
        confidence = min(confidence, 0.5)

    return SourceSelectionOutput(
        dimension_to_connector=resolved,
        rationale=rationale,
        confidence=confidence,
    )
