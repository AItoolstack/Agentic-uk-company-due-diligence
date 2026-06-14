"""
agents/followup_planner.py
FollowUpPlannerAgent -- decides whether and how to iterate research.

Reads:  state["gap_detection"], state["iteration_count"]
Writes: state["followup_plan"], (increments state["iteration_count"] if iterating)
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import settings
from src.llm import get_llm
from src.prompts import FOLLOWUP_PLANNER_SYSTEM, FOLLOWUP_PLANNER_USER
from src.schemas import FollowUpPlanOutput
from src.state import AgentState
from src.tracing import tracer


def followup_planner_agent(state: AgentState) -> dict:
    """Plan the next research iteration if gaps warrant it."""
    tracer.log_agent_start("FollowUpPlannerAgent", state)

    gap = state.get("gap_detection")
    iteration = max(state.get("iteration_count", 1), 1)

    missing = [d.value for d in gap.missing_dimensions] if gap else []
    partial = [d.value for d in gap.partially_covered] if gap else []
    gap_score = gap.gap_score if gap else 0.0

    llm = get_llm(tier="fast")
    structured = llm.with_structured_output(FollowUpPlanOutput)

    messages = [
        SystemMessage(content=FOLLOWUP_PLANNER_SYSTEM),
        HumanMessage(content=FOLLOWUP_PLANNER_USER.format(
            iteration=iteration,
            max_iterations=settings.max_followup_iterations,
            gap_score=gap_score,
            confidence_threshold=settings.confidence_threshold,
            missing_dimensions=", ".join(missing) or "none",
            partially_covered=", ".join(partial) or "none",
        )),
    ]

    try:
        output: FollowUpPlanOutput = structured.invoke(messages)

        # Hard-limit: override LLM if we have hit max iterations
        if iteration >= settings.max_followup_iterations:
            output = FollowUpPlanOutput(
                should_iterate=False,
                dimensions_to_retry=[],
                rationale=f"Max iterations ({settings.max_followup_iterations}) reached.",
                confidence=1.0,
            )

        update: dict = {"followup_plan": output}
        if output.should_iterate:
            update["iteration_count"] = iteration + 1
            tracer.log_iteration(iteration + 1, output.rationale)

        tracer.log_agent_end("FollowUpPlannerAgent", output)
        return update

    except Exception as e:
        tracer.log_error("FollowUpPlannerAgent", e)
        fallback = FollowUpPlanOutput(
            should_iterate=False,
            dimensions_to_retry=[],
            rationale="No follow-up (planner error).",
            confidence=0.3,
        )
        errors = list(state.get("errors", []))
        errors.append({"agent": "followup_planner", "error": str(e)})
        return {"followup_plan": fallback, "errors": errors}
