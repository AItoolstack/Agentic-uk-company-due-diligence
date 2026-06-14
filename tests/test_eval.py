"""
tests/test_eval.py
-------------------
Pytest-based evaluation harness.

Runs the compiled graph with mock data (USE_MOCK_DATA=true) against each
sample query and validates the DueDiligenceBrief using validate_brief.

These are integration tests -- they exercise the full graph including all
LLM agent nodes. Requires an LLM API key in .env (or env vars).

Mark slow with: pytest -m eval
Skip LLM calls: not easily skippable since agents are LLM-backed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.evaluation.sample_queries import SAMPLE_QUERIES
from src.evaluation.trace_validator import validate_brief
from src.graph import compiled_graph
from src.schemas import DueDiligenceBrief
from src.state import create_initial_state


def _run(query: str) -> dict[str, Any]:
    """Invoke the compiled graph with mock data."""
    return asyncio.run(compiled_graph.ainvoke(create_initial_state(query)))


def _brief(result: dict[str, Any]) -> DueDiligenceBrief:
    brief = result.get("due_diligence_brief")
    assert brief is not None, "due_diligence_brief missing from output state"
    assert isinstance(brief, DueDiligenceBrief)
    return brief


# -- Structural graph tests (no LLM, no API keys needed) ----------------------

class TestGraphStructure:
    """Verify the compiled graph has the right topology."""

    def test_graph_compiles(self):
        assert compiled_graph is not None

    def test_parallel_source_collector_registered(self):
        assert "parallel_source_collector" in compiled_graph.nodes

    def test_required_nodes_present(self):
        required = {
            "query_understanding", "entity_resolution", "research_planner",
            "source_selector", "parallel_source_collector", "news_classifier",
            "evidence_critic", "gap_detector", "followup_planner",
            "contradiction_detector", "synthesis",
        }
        missing = required - set(compiled_graph.nodes.keys())
        assert not missing, f"Missing nodes: {missing}"

    def test_news_classification_runs_before_evidence_critique(self):
        edges = {
            (edge.source, edge.target)
            for edge in compiled_graph.get_graph().edges
        }
        assert (
            "parallel_source_collector",
            "news_classifier",
        ) in edges
        assert ("news_classifier", "evidence_critic") in edges

    def test_individual_source_nodes_retained(self):
        """Individual source nodes are kept for direct testing / future Send."""
        for node in ("company_profile", "officers", "filing_history", "fca", "news"):
            assert node in compiled_graph.nodes


# -- Parallel collector unit tests (no LLM needed) -----------------------------

class TestParallelSourceCollector:
    """Unit-test the parallel_source_collector async node directly."""

    def test_collects_all_dimensions_mock(self):
        import asyncio
        from src.agents.parallel_source_collector import parallel_source_collector
        from src.schemas import ResearchDimension, SourceSelectionOutput
        from src.schemas import EntityResolutionOutput

        state = {
            "user_query": "test",
            "iteration_count": 1,
            "errors": [],
            "evidence_by_dimension": {},
            "entity_resolution": EntityResolutionOutput(
                company_name="MONZO BANK LIMITED",
                company_number="09446231",
                fca_firm_reference="730427",
                confidence=0.95,
            ),
            "source_selection": SourceSelectionOutput(
                dimension_to_connector={
                    ResearchDimension.COMPANY_PROFILE.value: "companies_house",
                    ResearchDimension.OFFICERS.value: "companies_house",
                    ResearchDimension.NEWS_SIGNALS.value: "brave_search",
                },
                rationale="test",
                confidence=1.0,
            ),
        }

        result = asyncio.run(parallel_source_collector(state))
        evidence = result["evidence_by_dimension"]

        assert ResearchDimension.COMPANY_PROFILE.value in evidence
        assert ResearchDimension.OFFICERS.value in evidence
        assert ResearchDimension.NEWS_SIGNALS.value in evidence

    def test_handles_unsupported_source_route_gracefully(self):
        import asyncio
        from src.agents.parallel_source_collector import parallel_source_collector
        from src.schemas import EvidenceQuality, SourceSelectionOutput

        state = {
            "user_query": "test",
            "iteration_count": 1,
            "errors": [],
            "evidence_by_dimension": {},
            "source_selection": SourceSelectionOutput(
                dimension_to_connector={
                    "company_profile": "open_sanctions"
                },
                rationale="test",
                confidence=0.5,
            ),
        }
        result = asyncio.run(parallel_source_collector(state))
        evidence = result["evidence_by_dimension"]["company_profile"]
        assert evidence.quality == EvidenceQuality.MISSING


# -- Full eval harness (requires LLM API key) ----------------------------------

@pytest.mark.eval
@pytest.mark.parametrize("sample", SAMPLE_QUERIES, ids=[s["id"] for s in SAMPLE_QUERIES])
def test_sample_query_produces_valid_brief(sample):
    """Full end-to-end: graph run + structural brief validation."""
    result = _run(sample["query"])

    # No uncaught exceptions from state errors
    errors = result.get("errors", [])
    assert len(errors) == 0, f"Agent errors in state: {errors}"

    brief = _brief(result)
    validation = validate_brief(brief, sample)

    assert validation.passed, (
        f"Brief validation failed for '{sample['id']}':\n"
        + "\n".join(f"  - {f}" for f in validation.failures)
    )
