"""
agents/synthesis_agent.py
--------------------------
SynthesisAgent -- final node; produces the structured due diligence brief.

Reads:  state["entity_resolution"], state["evidence_by_dimension"],
        state["evidence_critique"], state["gap_detection"],
        state["contradiction_detection"], state["iteration_count"]
Writes: state["due_diligence_brief"]

The LLM produces a SynthesisAnalysis (text summaries + risks + confidence).
The agent code assembles the full DueDiligenceBrief by merging this with
state metadata (evidence items, dimensions covered/missing, timestamps).
This keeps datetime fields and raw evidence data out of the LLM's contract.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.evidence_facts import extract_underwriting_evidence_facts
from src.llm import get_llm
from src.news_facts import extract_classified_news_signals
from src.prompts import SYNTHESIS_SYSTEM, SYNTHESIS_USER
from src.schemas import ResearchDimension, SynthesisAnalysis, UnderwritingAssessment
from src.state import AgentState
from src.tracing import tracer
from src.utils import flatten_evidence_summary


def synthesis_agent(state: AgentState) -> dict:
    """Synthesise all evidence into a structured due diligence brief."""
    tracer.log_agent_start("SynthesisAgent", state)

    er = state.get("entity_resolution")
    evidence = state.get("evidence_by_dimension", {})
    gap = state.get("gap_detection")
    contradiction = state.get("contradiction_detection")
    iteration = state.get("iteration_count", 1)

    company_name = er.company_name if er else "Unknown"
    company_number = er.company_number if er else None

    missing_dims = [d.value for d in gap.missing_dimensions] if gap else []
    contradictions_list = contradiction.contradictions if contradiction else []
    contradictions_text = (
        "\n".join(
            f"- [{c.severity.value}] {c.dimension.value}: {c.description} "
            f"(Sources: {c.source_a} vs {c.source_b})"
            for c in contradictions_list
        ) or "None detected."
    )

    llm = get_llm()
    structured = llm.with_structured_output(SynthesisAnalysis)

    messages = [
        SystemMessage(content=SYNTHESIS_SYSTEM),
        HumanMessage(content=SYNTHESIS_USER.format(
            company_name=company_name,
            company_number=company_number or "unknown",
            evidence_summary=flatten_evidence_summary(evidence),
            contradictions=contradictions_text,
            gaps=", ".join(missing_dims) or "none",
            iterations=iteration,
        )),
    ]

    # Carry forward any errors accumulated by earlier nodes.
    errors: list = list(state.get("errors") or [])

    try:
        analysis: SynthesisAnalysis = structured.invoke(messages)
    except Exception as e:
        tracer.log_error("SynthesisAgent", e)
        analysis = SynthesisAnalysis(
            company_status_summary="Synthesis failed -- see errors.",
            officers_summary="",
            filing_activity_summary="",
            regulatory_summary="",
            news_signals_summary="",
            key_risks=[],
            overall_confidence=0.1,
            confidence_rationale=f"LLM synthesis error: {e}",
        )
        errors.append({"agent": "synthesis", "error": str(e)})

    # -- Assemble full DueDiligenceBrief from LLM analysis + state metadata ---
    covered = [ResearchDimension(k) for k in evidence.keys() if evidence[k].confidence > 0.0]
    missing = [ResearchDimension(d) for d in missing_dims if d in ResearchDimension._value2member_map_]
    evidence_facts = extract_underwriting_evidence_facts(evidence)
    news_signals = extract_classified_news_signals(evidence)

    brief = UnderwritingAssessment(
        # -- Core identity -------------------------------------------------
        company_name=company_name,
        company_number=company_number,
        # -- Dimensional text summaries ------------------------------------
        company_status_summary=analysis.company_status_summary,
        officers_summary=analysis.officers_summary,
        filing_activity_summary=analysis.filing_activity_summary,
        regulatory_summary=analysis.regulatory_summary,
        news_signals_summary=analysis.news_signals_summary,
        fraud_signals_summary=analysis.fraud_signals_summary,
        beneficial_ownership_summary=analysis.beneficial_ownership_summary,
        web_evidence_summary=analysis.web_evidence_summary,
        # -- Risk assessment -----------------------------------------------
        risk_matrix=analysis.risk_matrix,
        key_risks=analysis.key_risks,
        overall_risk_level=analysis.overall_risk_level,
        # -- Structured news signals (LLM-extracted) -----------------------
        news_signals=news_signals,
        # -- Insurance-specific outputs ------------------------------------
        referral_triggers=analysis.referral_triggers,
        coverage_exclusions=analysis.coverage_exclusions,
        loading_indicators=analysis.loading_indicators,
        decline_indicators=analysis.decline_indicators,
        applicable_lines=analysis.applicable_lines,
        disqualified_officers=evidence_facts.disqualified_officers,
        phoenix_risk_score=evidence_facts.phoenix_risk_score,
        phoenix_evidence=evidence_facts.phoenix_evidence,
        sanctions_hits=evidence_facts.sanctions_hits,
        enforcement_actions=evidence_facts.enforcement_actions,
        psc_risk_flags=evidence_facts.psc_risk_flags,
        # -- Evidence trace ------------------------------------------------
        contradictions_detected=contradictions_list,
        evidence_items=list(evidence.values()),
        dimensions_covered=covered,
        dimensions_missing=missing,
        # -- Confidence ----------------------------------------------------
        overall_confidence=analysis.overall_confidence,
        confidence_rationale=analysis.confidence_rationale,
        iterations_performed=iteration,
    )

    tracer.log_agent_end("SynthesisAgent", brief)
    return {"due_diligence_brief": brief, "errors": errors}
