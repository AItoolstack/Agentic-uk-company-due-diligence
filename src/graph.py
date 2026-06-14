"""
graph.py
LangGraph research graph definition. Topology only -- no business logic.

Flow:
  query_understanding -> entity_resolution -> research_planner -> source_selector
  -> parallel_source_collector  (all sources run concurrently via asyncio.gather)
  -> news_classifier -> evidence_critic -> gap_detector -> followup_planner
  -> [conditional: iterate->research_planner | continue->contradiction_detector]
  -> synthesis -> END
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agents.company_profile_agent import company_profile_agent
from src.agents.contradiction_detector import contradiction_detector_agent
from src.agents.entity_resolution import entity_resolution_agent
from src.agents.evidence_critic import evidence_critic_agent
from src.agents.fca_agent import fca_agent
from src.agents.filing_history_agent import filing_history_agent
from src.agents.followup_planner import followup_planner_agent
from src.agents.gap_detector import gap_detector_agent
from src.agents.news_agent import news_agent
from src.agents.news_classifier import news_classifier_agent
from src.agents.officers_agent import officers_agent
from src.agents.parallel_source_collector import parallel_source_collector
from src.agents.query_understanding import query_understanding_agent
from src.agents.research_planner import research_planner_agent
from src.agents.source_selector import source_selector_agent
from src.agents.synthesis_agent import synthesis_agent
from src.config import settings
from src.state import AgentState


def _should_iterate(state: AgentState) -> str:
    """Conditional edge: loop back for follow-up research or continue."""
    followup = state.get("followup_plan")
    iteration = state.get("iteration_count", 1)
    if followup and followup.should_iterate and iteration <= settings.max_followup_iterations:
        return "iterate"
    return "continue"


def build_graph():
    """Construct and compile the research agent graph."""
    graph = StateGraph(AgentState)

    # Planning nodes
    graph.add_node("query_understanding", query_understanding_agent)
    graph.add_node("entity_resolution", entity_resolution_agent)
    graph.add_node("research_planner", research_planner_agent)
    graph.add_node("source_selector", source_selector_agent)

    # Parallel source collection replaces sequential source calls.
    # Runs all selected retrievers concurrently via asyncio.gather + asyncio.to_thread.
    graph.add_node("parallel_source_collector", parallel_source_collector)
    graph.add_node("news_classifier", news_classifier_agent)

    # Individual source nodes -- kept for direct unit testing and future Send fan-out
    graph.add_node("company_profile", company_profile_agent)
    graph.add_node("officers", officers_agent)
    graph.add_node("filing_history", filing_history_agent)
    graph.add_node("fca", fca_agent)
    graph.add_node("news", news_agent)

    # Analysis nodes
    graph.add_node("evidence_critic", evidence_critic_agent)
    graph.add_node("gap_detector", gap_detector_agent)
    graph.add_node("followup_planner", followup_planner_agent)
    graph.add_node("contradiction_detector", contradiction_detector_agent)
    graph.add_node("synthesis", synthesis_agent)

    # Main flow edges
    graph.set_entry_point("query_understanding")
    graph.add_edge("query_understanding", "entity_resolution")
    graph.add_edge("entity_resolution", "research_planner")
    graph.add_edge("research_planner", "source_selector")
    graph.add_edge("source_selector", "parallel_source_collector")
    graph.add_edge("parallel_source_collector", "news_classifier")
    graph.add_edge("news_classifier", "evidence_critic")

    graph.add_edge("evidence_critic", "gap_detector")
    graph.add_edge("gap_detector", "followup_planner")

    graph.add_conditional_edges(
        "followup_planner",
        _should_iterate,
        {"iterate": "research_planner", "continue": "contradiction_detector"},
    )

    graph.add_edge("contradiction_detector", "synthesis")
    graph.add_edge("synthesis", END)

    return graph.compile()


# Compiled singleton -- import this in app.py
compiled_graph = build_graph()
