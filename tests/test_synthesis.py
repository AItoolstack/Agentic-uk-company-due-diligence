"""Tests for deterministic evidence handoff into the final assessment."""

from __future__ import annotations

from datetime import datetime

from src.evidence_facts import extract_underwriting_evidence_facts
from src.schemas import (
    EntityResolutionOutput,
    EvidenceItem,
    EvidenceQuality,
    ResearchDimension,
    SynthesisAnalysis,
    UnderwritingAssessment,
)


def _evidence(
    dimension: ResearchDimension,
    raw_data: dict,
) -> EvidenceItem:
    return EvidenceItem(
        source="test",
        dimension=dimension,
        retrieved_at=datetime(2026, 6, 14),
        raw_data=raw_data,
        summary="test evidence",
        quality=EvidenceQuality.HIGH,
        confidence=0.9,
    )


def _evidence_map() -> dict[str, EvidenceItem]:
    return {
        ResearchDimension.FRAUD_SIGNALS.value: _evidence(
            ResearchDimension.FRAUD_SIGNALS,
            {
                "disqualified_officers": ["Alex Smith"],
                "phoenix_risk_score": 0.72,
                "phoenix_evidence": ["Alex Smith: 3/5 companies failed"],
                "sanctions_hits": ["Shared sanctions hit", "Director sanctions hit"],
                "enforcement_actions": ["Fraud enforcement action"],
            },
        ),
        ResearchDimension.BENEFICIAL_OWNERSHIP.value: _evidence(
            ResearchDimension.BENEFICIAL_OWNERSHIP,
            {
                "psc_risk_flags": ["HIGH-RISK OFFSHORE PSC: Example Holdings"],
                "sanctions_hits": ["Shared sanctions hit", "PSC sanctions hit"],
            },
        ),
        ResearchDimension.REGULATORY_STATUS.value: _evidence(
            ResearchDimension.REGULATORY_STATUS,
            {
                "disciplinary_history": [
                    {
                        "description": "FCA public censure",
                        "reference": "ENF-123",
                        "date": "2025-04-10",
                    }
                ],
                "enforcement_actions": ["Restriction on regulated activity"],
            },
        ),
        ResearchDimension.NEWS_SIGNALS.value: _evidence(
            ResearchDimension.NEWS_SIGNALS,
            {
                "classification_status": "completed",
                "signals": [
                    {
                        "category": "operational_incident",
                        "headline": "Classified source headline",
                        "source_url": "https://example.test/classified",
                        "date": "2026-06-01",
                        "severity": "high",
                        "summary": "Customer records were exposed.",
                        "classification_rationale":
                            "Confirmed material data exposure.",
                        "classification_confidence": 0.91,
                    }
                ],
            },
        ),
    }


def test_extract_underwriting_evidence_facts() -> None:
    facts = extract_underwriting_evidence_facts(_evidence_map())

    assert facts.disqualified_officers == ["Alex Smith"]
    assert facts.phoenix_risk_score == 0.72
    assert facts.phoenix_evidence == ["Alex Smith: 3/5 companies failed"]
    assert facts.sanctions_hits == [
        "Shared sanctions hit",
        "Director sanctions hit",
        "PSC sanctions hit",
    ]
    assert facts.psc_risk_flags == ["HIGH-RISK OFFSHORE PSC: Example Holdings"]
    assert facts.enforcement_actions == [
        "Restriction on regulated activity",
        "FCA public censure | ENF-123 | 2025-04-10",
        "Fraud enforcement action",
    ]


def test_synthesis_preserves_authoritative_evidence_facts(monkeypatch) -> None:
    from src.agents import synthesis_agent as synthesis_module

    analysis = SynthesisAnalysis(
        company_status_summary="Active company.",
        officers_summary="Officer review complete.",
        filing_activity_summary="Filings reviewed.",
        regulatory_summary="Regulatory evidence reviewed.",
        news_signals_summary="News reviewed.",
        fraud_signals_summary="Elevated phoenix-company risk identified.",
        beneficial_ownership_summary="Ownership risk identified.",
        overall_confidence=0.8,
        confidence_rationale="Authoritative evidence was available.",
    )

    class FakeStructuredLLM:
        def invoke(self, messages: object) -> SynthesisAnalysis:
            return analysis

    class FakeLLM:
        def with_structured_output(self, schema: type) -> FakeStructuredLLM:
            assert schema is SynthesisAnalysis
            return FakeStructuredLLM()

    monkeypatch.setattr(synthesis_module, "get_llm", lambda: FakeLLM())

    result = synthesis_module.synthesis_agent(
        {
            "entity_resolution": EntityResolutionOutput(
                company_name="EXAMPLE LIMITED",
                company_number="01234567",
                confidence=0.95,
            ),
            "evidence_by_dimension": _evidence_map(),
            "iteration_count": 1,
            "errors": [],
        }
    )

    brief = result["due_diligence_brief"]
    assert isinstance(brief, UnderwritingAssessment)
    assert brief.disqualified_officers == ["Alex Smith"]
    assert brief.phoenix_risk_score == 0.72
    assert brief.phoenix_evidence == ["Alex Smith: 3/5 companies failed"]
    assert brief.sanctions_hits == [
        "Shared sanctions hit",
        "Director sanctions hit",
        "PSC sanctions hit",
    ]
    assert brief.psc_risk_flags == ["HIGH-RISK OFFSHORE PSC: Example Holdings"]
    assert brief.enforcement_actions == [
        "Restriction on regulated activity",
        "FCA public censure | ENF-123 | 2025-04-10",
        "Fraud enforcement action",
    ]
    assert [signal.headline for signal in brief.news_signals] == [
        "Classified source headline"
    ]
    assert brief.news_signals[0].classification_confidence == 0.91
