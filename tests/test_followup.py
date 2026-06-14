"""Tests for follow-up pass counting and cumulative evidence behavior."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from src.evidence_merge import merge_evidence_by_dimension
from src.schemas import (
    ConnectorName,
    EntityResolutionOutput,
    EvidenceItem,
    EvidenceQuality,
    FollowUpPlanOutput,
    GapDetectionOutput,
    ResearchDimension,
    SourceSelectionOutput,
)
from src.state import create_initial_state


def _evidence(
    dimension: ResearchDimension,
    quality: EvidenceQuality,
    confidence: float,
    summary: str,
    retrieved_at: datetime,
) -> EvidenceItem:
    return EvidenceItem(
        source="test",
        dimension=dimension,
        retrieved_at=retrieved_at,
        summary=summary,
        quality=quality,
        confidence=confidence,
    )


def test_initial_state_starts_on_first_research_pass() -> None:
    state = create_initial_state("Research Example Limited")

    assert state == {
        "user_query": "Research Example Limited",
        "iteration_count": 1,
        "errors": [],
        "evidence_by_dimension": {},
    }


def test_merge_preserves_dimensions_and_rejects_weaker_retry() -> None:
    now = datetime(2026, 6, 14, 10, 0, 0)
    profile = _evidence(
        ResearchDimension.COMPANY_PROFILE,
        EvidenceQuality.HIGH,
        0.95,
        "Authoritative company profile",
        now,
    )
    failed_profile_retry = _evidence(
        ResearchDimension.COMPANY_PROFILE,
        EvidenceQuality.MISSING,
        0.0,
        "Retry failed",
        now + timedelta(minutes=1),
    )
    fraud = _evidence(
        ResearchDimension.FRAUD_SIGNALS,
        EvidenceQuality.MEDIUM,
        0.8,
        "Fraud screening completed",
        now + timedelta(minutes=1),
    )

    merged = merge_evidence_by_dimension(
        {ResearchDimension.COMPANY_PROFILE.value: profile},
        {
            ResearchDimension.COMPANY_PROFILE.value: failed_profile_retry,
            ResearchDimension.FRAUD_SIGNALS.value: fraud,
        },
    )

    assert merged[ResearchDimension.COMPANY_PROFILE.value] is profile
    assert merged[ResearchDimension.FRAUD_SIGNALS.value] is fraud


def test_merge_replaces_existing_evidence_with_stronger_retry() -> None:
    now = datetime(2026, 6, 14, 10, 0, 0)
    weak = _evidence(
        ResearchDimension.REGULATORY_STATUS,
        EvidenceQuality.LOW,
        0.3,
        "Thin regulatory evidence",
        now,
    )
    strong = _evidence(
        ResearchDimension.REGULATORY_STATUS,
        EvidenceQuality.HIGH,
        0.95,
        "Authoritative FCA evidence",
        now + timedelta(minutes=1),
    )

    merged = merge_evidence_by_dimension(
        {ResearchDimension.REGULATORY_STATUS.value: weak},
        {ResearchDimension.REGULATORY_STATUS.value: strong},
    )

    assert merged[ResearchDimension.REGULATORY_STATUS.value] is strong


def test_merge_compares_naive_and_aware_timestamps() -> None:
    dimension = ResearchDimension.FRAUD_SIGNALS.value
    existing = _evidence(
        ResearchDimension.FRAUD_SIGNALS,
        EvidenceQuality.MEDIUM,
        0.7,
        "First-pass fraud evidence",
        datetime(2026, 6, 14, 8, 0),
    )
    newer = _evidence(
        ResearchDimension.FRAUD_SIGNALS,
        EvidenceQuality.MEDIUM,
        0.7,
        "Newer retry evidence",
        datetime(2026, 6, 14, 9, 0, tzinfo=timezone.utc),
    )

    merged = merge_evidence_by_dimension(
        {dimension: existing},
        {dimension: newer},
    )

    assert merged[dimension] is newer


def test_parallel_collector_merges_targeted_retry(monkeypatch) -> None:
    from src.agents import parallel_source_collector as collector_module

    now = datetime(2026, 6, 14, 10, 0, 0)
    profile = _evidence(
        ResearchDimension.COMPANY_PROFILE,
        EvidenceQuality.HIGH,
        0.95,
        "First-pass company profile",
        now,
    )
    fraud = _evidence(
        ResearchDimension.FRAUD_SIGNALS,
        EvidenceQuality.HIGH,
        0.9,
        "Second-pass fraud evidence",
        now + timedelta(minutes=1),
    )

    async def fake_collect_all(
        assignments: dict[ResearchDimension, ConnectorName],
        company_number: str,
        company_name: str,
        firm_reference: str,
    ) -> dict[str, EvidenceItem]:
        assert assignments == {
            ResearchDimension.FRAUD_SIGNALS:
                ConnectorName.COMPANIES_HOUSE_FRAUD
        }
        return {ResearchDimension.FRAUD_SIGNALS.value: fraud}

    monkeypatch.setattr(collector_module, "_collect_all", fake_collect_all)

    result = asyncio.run(
        collector_module.parallel_source_collector(
            {
                "entity_resolution": EntityResolutionOutput(
                    company_name="EXAMPLE LIMITED",
                    company_number="01234567",
                    confidence=0.95,
                ),
                "source_selection": SourceSelectionOutput(
                    dimension_to_connector={
                        ResearchDimension.FRAUD_SIGNALS.value: "companies_house_fraud"
                    },
                    rationale="Retry fraud screening",
                    confidence=1.0,
                ),
                "evidence_by_dimension": {
                    ResearchDimension.COMPANY_PROFILE.value: profile
                },
                "iteration_count": 2,
            }
        )
    )

    evidence = result["evidence_by_dimension"]
    assert evidence[ResearchDimension.COMPANY_PROFILE.value] is profile
    assert evidence[ResearchDimension.FRAUD_SIGNALS.value] is fraud
    assert result["collected_dimensions_this_pass"] == [
        ResearchDimension.FRAUD_SIGNALS.value
    ]
    assert result["source_footprint_this_pass"] == {
        ResearchDimension.FRAUD_SIGNALS.value: [
            "companies_house_fraud",
            "open_sanctions",
        ]
    }


def test_sse_detail_reports_only_dimensions_collected_this_pass() -> None:
    from src.api.routes import _extract_node_detail

    detail = _extract_node_detail(
        "parallel_source_collector",
        {
            "evidence_by_dimension": {
                ResearchDimension.COMPANY_PROFILE.value: object(),
                ResearchDimension.OFFICERS.value: object(),
                ResearchDimension.FRAUD_SIGNALS.value: object(),
            },
            "collected_dimensions_this_pass": [
                ResearchDimension.FRAUD_SIGNALS.value
            ],
        },
    )

    assert detail == "Collected 1 dimension in parallel"


def test_followup_advances_from_pass_one_to_pass_two(monkeypatch) -> None:
    from src.agents import followup_planner as followup_module
    from src.graph import _should_iterate

    class FakeStructuredLLM:
        def invoke(self, messages: object) -> FollowUpPlanOutput:
            return FollowUpPlanOutput(
                should_iterate=True,
                dimensions_to_retry=[ResearchDimension.FRAUD_SIGNALS],
                rationale="Retry missing fraud evidence.",
                confidence=0.9,
            )

    class FakeLLM:
        def with_structured_output(self, schema: type) -> FakeStructuredLLM:
            assert schema is FollowUpPlanOutput
            return FakeStructuredLLM()

    monkeypatch.setattr(followup_module, "get_llm", lambda tier: FakeLLM())
    monkeypatch.setattr(followup_module.settings, "max_followup_iterations", 2)

    gap = GapDetectionOutput(
        missing_dimensions=[ResearchDimension.FRAUD_SIGNALS],
        partially_covered=[],
        gap_score=0.5,
        follow_up_needed=True,
        confidence=0.9,
    )
    first_update = followup_module.followup_planner_agent(
        {"gap_detection": gap, "iteration_count": 1}
    )

    assert first_update["iteration_count"] == 2
    assert first_update["followup_plan"].should_iterate is True
    assert _should_iterate(first_update) == "iterate"

    second_update = followup_module.followup_planner_agent(
        {"gap_detection": gap, "iteration_count": 2}
    )

    assert "iteration_count" not in second_update
    assert second_update["followup_plan"].should_iterate is False
    assert _should_iterate(
        {**second_update, "iteration_count": 2}
    ) == "continue"
