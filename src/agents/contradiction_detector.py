"""
agents/contradiction_detector.py
----------------------------------
ContradictionDetectorAgent -- surfaces conflicting evidence across sources.

Reads:  state["evidence_by_dimension"]
Writes: state["contradiction_detection"]
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm import get_llm
from src.prompts import CONTRADICTION_DETECTOR_SYSTEM, CONTRADICTION_DETECTOR_USER
from src.schemas import ContradictionDetectionOutput
from src.state import AgentState
from src.tracing import tracer
from src.utils import flatten_evidence_summary


def contradiction_detector_agent(state: AgentState) -> dict:
    """Detect contradictions and discrepancies across evidence sources."""
    tracer.log_agent_start("ContradictionDetectorAgent", state)

    evidence = state.get("evidence_by_dimension", {})

    if len(evidence) < 2:
        # Nothing to cross-check
        output = ContradictionDetectionOutput(
            contradictions=[],
            contradiction_count=0,
            confidence=1.0,
        )
        tracer.log_agent_end("ContradictionDetectorAgent", output)
        return {"contradiction_detection": output}

    llm = get_llm()
    structured = llm.with_structured_output(ContradictionDetectionOutput)

    messages = [
        SystemMessage(content=CONTRADICTION_DETECTOR_SYSTEM),
        HumanMessage(content=CONTRADICTION_DETECTOR_USER.format(
            evidence_summary=flatten_evidence_summary(evidence),
        )),
    ]

    try:
        output: ContradictionDetectionOutput = structured.invoke(messages)
        # Sync count field with actual list length
        output = ContradictionDetectionOutput(
            contradictions=output.contradictions,
            contradiction_count=len(output.contradictions),
            confidence=output.confidence,
        )
        tracer.log_agent_end("ContradictionDetectorAgent", output)
        return {"contradiction_detection": output}
    except Exception as e:
        tracer.log_error("ContradictionDetectorAgent", e)
        fallback = ContradictionDetectionOutput(
            contradictions=[],
            contradiction_count=0,
            confidence=0.3,
        )
        errors = list(state.get("errors", []))
        errors.append({"agent": "contradiction_detector", "error": str(e)})
        return {"contradiction_detection": fallback, "errors": errors}
