"""
state.py
--------
LangGraph AgentState definition.

All agent nodes read from and write partial updates to this single state object.
Never scatter state definitions across agent files -- everything lives here.
"""

from __future__ import annotations

from typing import Any, TypedDict

from src.schemas import (
    ContradictionDetectionOutput,
    DueDiligenceBrief,
    UnderwritingAssessment,
    EntityResolutionOutput,
    EvidenceCritiqueOutput,
    EvidenceItem,
    FollowUpPlanOutput,
    GapDetectionOutput,
    NewsClassificationOutput,
    QueryUnderstandingOutput,
    ResearchDimension,
    ResearchPlanOutput,
    SourceSelectionOutput,
)


class AgentState(TypedDict, total=False):
    # -- Input ----------------------------------------------------------------
    user_query: str

    # -- Query understanding ---------------------------------------------------
    query_understanding: QueryUnderstandingOutput

    # -- Entity resolution ----------------------------------------------------
    entity_resolution: EntityResolutionOutput

    # -- Research planning -----------------------------------------------------
    research_plan: ResearchPlanOutput
    source_selection: SourceSelectionOutput

    # -- Evidence collection ---------------------------------------------------
    # Keyed by ResearchDimension value string
    evidence_by_dimension: dict[str, EvidenceItem]
    collected_dimensions_this_pass: list[str]
    source_footprint_this_pass: dict[str, list[str]]
    news_classification: NewsClassificationOutput

    # -- Evidence quality -----------------------------------------------------
    evidence_critique: EvidenceCritiqueOutput

    # -- Gap detection / follow-up ---------------------------------------------
    gap_detection: GapDetectionOutput
    followup_plan: FollowUpPlanOutput
    iteration_count: int

    # -- Contradiction detection -----------------------------------------------
    contradiction_detection: ContradictionDetectionOutput

    # -- Final output ----------------------------------------------------------
    due_diligence_brief: UnderwritingAssessment

    # -- Error handling --------------------------------------------------------
    errors: list[dict[str, Any]]


def create_initial_state(user_query: str) -> AgentState:
    """Canonical initial AgentState for a research run.

    Both the API layer and the CLI eval runner must start from this exact
    state so iteration counting and evidence accumulation are identical across
    entry points. The first research pass is iteration 1; follow-ups increment.
    """
    return {
        "user_query": user_query,
        "iteration_count": 1,
        "errors": [],
        "evidence_by_dimension": {},
    }
