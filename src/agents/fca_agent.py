"""
agents/fca_agent.py
--------------------
FCAAgent -- retrieves FCA Register regulatory data.

Reads:  state["entity_resolution"]
Writes: state["evidence_by_dimension"]["regulatory_status"]
"""

from __future__ import annotations

from src.retrievers.fca_retriever import FCARetriever
from src.schemas import ResearchDimension
from src.state import AgentState
from src.tracing import tracer


def fca_agent(state: AgentState) -> dict:
    """Retrieve FCA Register regulatory status evidence."""
    tracer.log_agent_start("FCAAgent", state)

    er = state.get("entity_resolution")
    firm_reference = (er.fca_firm_reference or "") if er else ""
    company_number = (er.company_number or "") if er else ""

    retriever = FCARetriever()
    item = retriever.retrieve(company_number, firm_reference=firm_reference)

    evidence = dict(state.get("evidence_by_dimension", {}))
    evidence[ResearchDimension.REGULATORY_STATUS.value] = item

    tracer.log_agent_end("FCAAgent", item)
    return {"evidence_by_dimension": evidence}
