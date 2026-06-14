"""
agents/evidence_critic.py
--------------------------
EvidenceCriticAgent -- assesses quality of collected evidence.

Reads:  state["evidence_by_dimension"], state["research_plan"]
Writes: state["evidence_critique"]
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm import get_llm
from src.prompts import EVIDENCE_CRITIC_SYSTEM, EVIDENCE_CRITIC_USER
from src.schemas import EvidenceCritiqueOutput, EvidenceQuality
from src.state import AgentState
from src.tracing import tracer
from src.utils import flatten_evidence_summary


def evidence_critic_agent(state: AgentState) -> dict:
    """Critique the quality of all collected evidence."""
    tracer.log_agent_start("EvidenceCriticAgent", state)

    plan = state.get("research_plan")
    evidence = state.get("evidence_by_dimension", {})

    required_dimensions = (
        [d.value for d in plan.dimensions_to_investigate]
        if plan else list(evidence.keys())
    )

    llm = get_llm()
    structured = llm.with_structured_output(EvidenceCritiqueOutput)

    messages = [
        SystemMessage(content=EVIDENCE_CRITIC_SYSTEM),
        HumanMessage(content=EVIDENCE_CRITIC_USER.format(
            required_dimensions=", ".join(required_dimensions),
            evidence_summary=flatten_evidence_summary(evidence),
        )),
    ]

    try:
        output: EvidenceCritiqueOutput = structured.invoke(messages)
        tracer.log_agent_end("EvidenceCriticAgent", output)
        return {"evidence_critique": output}
    except Exception as e:
        tracer.log_error("EvidenceCriticAgent", e)
        # Build a passable fallback from actual evidence quality fields
        dim_quality = {
            dim: item.quality for dim, item in evidence.items()
        }
        fallback = EvidenceCritiqueOutput(
            dimension_quality=dim_quality,
            overall_quality_score=0.5,
            weak_dimensions=[
                d for d, q in dim_quality.items()
                if q in (EvidenceQuality.LOW, EvidenceQuality.MISSING)
            ],
            critique_notes="Fallback quality assessment (LLM critique failed).",
            confidence=0.3,
        )
        errors = list(state.get("errors", []))
        errors.append({"agent": "evidence_critic", "error": str(e)})
        return {"evidence_critique": fallback, "errors": errors}
