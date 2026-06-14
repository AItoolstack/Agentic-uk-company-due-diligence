"""
agents/research_planner.py
---------------------------
ResearchPlannerAgent -- decides which research dimensions to investigate.

Reads:  state["query_understanding"], state["entity_resolution"],
        state["gap_detection"] (on follow-up iterations)
Writes: state["research_plan"]
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm import get_llm
from src.prompts import RESEARCH_PLANNER_SYSTEM, RESEARCH_PLANNER_USER
from src.schemas import ResearchDimension, ResearchPlanOutput
from src.state import AgentState
from src.tracing import tracer


def research_planner_agent(state: AgentState) -> dict:
    """Produce a research plan for which dimensions to investigate."""
    tracer.log_agent_start("ResearchPlannerAgent", state)

    qu = state.get("query_understanding")
    er = state.get("entity_resolution")
    gap = state.get("gap_detection")
    iteration = state.get("iteration_count", 1)

    company_name = er.company_name if er else (qu.company_name if qu else "Unknown")
    research_objective = qu.research_objective if qu else "Full due diligence"
    requested = [d.value for d in (qu.requested_dimensions if qu else [])]
    previous_gaps = [d.value for d in gap.missing_dimensions] if gap else []
    previous_weak = [d.value for d in gap.partially_covered] if gap else []

    llm = get_llm()
    structured = llm.with_structured_output(ResearchPlanOutput)

    messages = [
        SystemMessage(content=RESEARCH_PLANNER_SYSTEM),
        HumanMessage(content=RESEARCH_PLANNER_USER.format(
            company_name=company_name,
            research_objective=research_objective,
            requested_dimensions=", ".join(requested) or "not specified",
            iteration=iteration,
            previous_gaps=", ".join(previous_gaps) or "none",
            previous_weak=", ".join(previous_weak) or "none",
        )),
    ]

    try:
        output: ResearchPlanOutput = structured.invoke(messages)
        tracer.log_agent_end("ResearchPlannerAgent", output)
        return {"research_plan": output}
    except Exception as e:
        tracer.log_error("ResearchPlannerAgent", e)
        # Fallback: use all dimensions
        fallback = ResearchPlanOutput(
            dimensions_to_investigate=list(ResearchDimension),
            rationale="Fallback: using all dimensions due to planning error.",
            confidence=0.3,
        )
        errors = list(state.get("errors", []))
        errors.append({"agent": "research_planner", "error": str(e)})
        return {"research_plan": fallback, "errors": errors}
