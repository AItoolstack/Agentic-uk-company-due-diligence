"""
agents/company_profile_agent.py
--------------------------------
CompanyProfileAgent -- retrieves Companies House company profile.

Reads:  state["entity_resolution"]
Writes: state["evidence_by_dimension"]["company_profile"]
"""

from __future__ import annotations

from src.retrievers.company_profile_retriever import CompanyProfileRetriever
from src.schemas import ResearchDimension
from src.state import AgentState
from src.tracing import tracer


def company_profile_agent(state: AgentState) -> dict:
    """Retrieve Companies House company profile evidence."""
    tracer.log_agent_start("CompanyProfileAgent", state)

    er = state.get("entity_resolution")
    company_number = (er.company_number or "") if er else ""

    retriever = CompanyProfileRetriever()
    item = retriever.retrieve(company_number)

    evidence = dict(state.get("evidence_by_dimension", {}))
    evidence[ResearchDimension.COMPANY_PROFILE.value] = item

    tracer.log_agent_end("CompanyProfileAgent", item)
    return {"evidence_by_dimension": evidence}
