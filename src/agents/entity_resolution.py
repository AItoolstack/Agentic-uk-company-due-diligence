"""
agents/entity_resolution.py
-----------------------------
EntityResolutionAgent -- resolves company name to canonical identifiers.

Reads:  state["query_understanding"]
Writes: state["entity_resolution"]

Strategy:
  1. Call CompaniesHouseConnector.search_company() for CH number.
  2. Call FCARegisterConnector.search_firm() for FCA FRN.
  3. If CH search returns 1 result -> use directly (high confidence).
  4. If ambiguous -> use LLM to pick best match.
  5. Return EntityResolutionOutput with all resolved identifiers.

In mock mode both connectors return deterministic results -- no LLM call needed.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from src.connectors.companies_house import CompaniesHouseConnector
from src.connectors.fca_register import FCARegisterConnector
from src.llm import get_llm
from src.prompts import (
    ENTITY_RESOLUTION_DISAMBIGUATION_SYSTEM,
    ENTITY_RESOLUTION_DISAMBIGUATION_USER,
)
from src.schemas import EntityResolutionOutput
from src.state import AgentState
from src.tracing import tracer


class _DisambiguationOutput(EntityResolutionOutput):
    """Thin subclass used only for LLM disambiguation -- no extra fields."""


def entity_resolution_agent(state: AgentState) -> dict:
    """Resolve company name to Companies House number and FCA FRN."""
    tracer.log_agent_start("EntityResolutionAgent", state)

    qu = state.get("query_understanding")
    company_name: str = qu.company_name if qu else state["user_query"]

    ch = CompaniesHouseConnector()
    fca = FCARegisterConnector()

    # -- Companies House search ------------------------------------------------
    company_number: str | None = None
    resolution_method = "connector_search"
    confidence = 0.5

    try:
        ch_results = ch.search_company(company_name)
        items: list[dict] = ch_results.get("items", [])

        if len(items) == 1:
            company_number = items[0].get("company_number")
            confidence = 0.95
        elif len(items) > 1:
            # Use LLM to disambiguate
            llm = get_llm()
            structured = llm.with_structured_output(EntityResolutionOutput)
            messages = [
                SystemMessage(content=ENTITY_RESOLUTION_DISAMBIGUATION_SYSTEM),
                HumanMessage(content=ENTITY_RESOLUTION_DISAMBIGUATION_USER.format(
                    company_name=company_name,
                    search_results=json.dumps(items, indent=2),
                )),
            ]
            result: EntityResolutionOutput = structured.invoke(messages)
            company_number = result.company_number
            confidence = result.confidence
            resolution_method = "llm_disambiguation"
        else:
            # No results -- fall back to query understanding name only
            confidence = 0.2

    except Exception as e:
        tracer.log_error("EntityResolutionAgent (CH search)", e)
        confidence = 0.1

    # -- FCA Register search ---------------------------------------------------
    fca_frn: str | None = None
    try:
        fca_results = fca.search_firm(company_name)
        fca_items: list[dict] = fca_results.get("Data", [])
        if fca_items:
            fca_frn = str(fca_items[0].get("FRN", ""))
    except Exception as e:
        tracer.log_error("EntityResolutionAgent (FCA search)", e)

    output = EntityResolutionOutput(
        company_name=company_name,
        company_number=company_number,
        fca_firm_reference=fca_frn,
        resolution_method=resolution_method,
        confidence=confidence,
        notes="" if company_number else "Company number not resolved -- using name only.",
    )

    tracer.log_agent_end("EntityResolutionAgent", output)
    return {"entity_resolution": output}
