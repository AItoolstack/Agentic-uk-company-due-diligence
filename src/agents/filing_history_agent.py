"""
agents/filing_history_agent.py
-------------------------------
FilingHistoryAgent -- retrieves Companies House filing history.

Reads:  state["entity_resolution"]
Writes: state["evidence_by_dimension"]["filing_history"]
"""

from __future__ import annotations

from src.retrievers.filing_history_retriever import FilingHistoryRetriever
from src.schemas import ResearchDimension
from src.state import AgentState
from src.tracing import tracer


def filing_history_agent(state: AgentState) -> dict:
    """Retrieve Companies House filing history evidence."""
    tracer.log_agent_start("FilingHistoryAgent", state)

    er = state.get("entity_resolution")
    company_number = (er.company_number or "") if er else ""

    retriever = FilingHistoryRetriever()
    item = retriever.retrieve(company_number)

    evidence = dict(state.get("evidence_by_dimension", {}))
    evidence[ResearchDimension.FILING_HISTORY.value] = item

    tracer.log_agent_end("FilingHistoryAgent", item)
    return {"evidence_by_dimension": evidence}
