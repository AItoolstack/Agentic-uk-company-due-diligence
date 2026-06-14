"""
agents/officers_agent.py
-------------------------
OfficersAgent -- retrieves Companies House officer list.

Reads:  state["entity_resolution"]
Writes: state["evidence_by_dimension"]["officers"]
"""

from __future__ import annotations

from src.retrievers.officers_retriever import OfficersRetriever
from src.schemas import ResearchDimension
from src.state import AgentState
from src.tracing import tracer


def officers_agent(state: AgentState) -> dict:
    """Retrieve Companies House officers evidence."""
    tracer.log_agent_start("OfficersAgent", state)

    er = state.get("entity_resolution")
    company_number = (er.company_number or "") if er else ""

    retriever = OfficersRetriever()
    item = retriever.retrieve(company_number)

    evidence = dict(state.get("evidence_by_dimension", {}))
    evidence[ResearchDimension.OFFICERS.value] = item

    tracer.log_agent_end("OfficersAgent", item)
    return {"evidence_by_dimension": evidence}
