"""
agents/news_agent.py
---------------------
NewsAgent -- retrieves news article evidence.

Reads:  state["entity_resolution"]
Writes: state["evidence_by_dimension"]["news_signals"]
"""

from __future__ import annotations

from src.retrievers.news_retriever import NewsRetriever
from src.schemas import ResearchDimension
from src.state import AgentState
from src.tracing import tracer


def news_agent(state: AgentState) -> dict:
    """Retrieve news signals evidence."""
    tracer.log_agent_start("NewsAgent", state)

    er = state.get("entity_resolution")
    company_name = er.company_name if er else ""

    retriever = NewsRetriever()
    item = retriever.retrieve(
        company_number=er.company_number or "" if er else "",
        company_name=company_name,
    )

    evidence = dict(state.get("evidence_by_dimension", {}))
    evidence[ResearchDimension.NEWS_SIGNALS.value] = item

    tracer.log_agent_end("NewsAgent", item)
    return {"evidence_by_dimension": evidence}
