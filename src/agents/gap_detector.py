"""
agents/gap_detector.py
-----------------------
GapDetectorAgent -- identifies missing or incomplete research dimensions.

Reads:  state["research_plan"], state["evidence_critique"]
Writes: state["gap_detection"]
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.config import settings
from src.llm import get_llm
from src.prompts import GAP_DETECTOR_SYSTEM, GAP_DETECTOR_USER
from src.schemas import GapDetectionOutput, ResearchDimension
from src.state import AgentState
from src.tracing import tracer


def gap_detector_agent(state: AgentState) -> dict:
    """Detect missing or weak research dimensions."""
    tracer.log_agent_start("GapDetectorAgent", state)

    plan = state.get("research_plan")
    critique = state.get("evidence_critique")
    er = state.get("entity_resolution")

    planned = [d.value for d in (plan.dimensions_to_investigate if plan else list(ResearchDimension))]
    dim_quality = {k: v.value for k, v in critique.dimension_quality.items()} if critique else {}
    company_context = "UK bank / FCA-regulated" if er and er.fca_firm_reference else "UK company"

    llm = get_llm()
    structured = llm.with_structured_output(GapDetectionOutput)

    messages = [
        SystemMessage(content=GAP_DETECTOR_SYSTEM),
        HumanMessage(content=GAP_DETECTOR_USER.format(
            planned_dimensions=", ".join(planned),
            dimension_quality=str(dim_quality),
            confidence_threshold=settings.confidence_threshold,
            company_context=company_context,
        )),
    ]

    try:
        output: GapDetectionOutput = structured.invoke(messages)
        tracer.log_agent_end("GapDetectorAgent", output)
        return {"gap_detection": output}
    except Exception as e:
        tracer.log_error("GapDetectorAgent", e)
        fallback = GapDetectionOutput(
            missing_dimensions=[],
            partially_covered=[],
            gap_score=0.0,
            follow_up_needed=False,
            confidence=0.3,
        )
        errors = list(state.get("errors", []))
        errors.append({"agent": "gap_detector", "error": str(e)})
        return {"gap_detection": fallback, "errors": errors}
