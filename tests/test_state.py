"""
tests/test_state.py
--------------------
Validates AgentState TypedDict structure and partial update patterns.
"""

from __future__ import annotations

import pytest

from src.state import AgentState, create_initial_state


class TestAgentState:
    def test_canonical_initial_state(self):
        state = create_initial_state("Monzo due diligence")
        assert state["iteration_count"] == 1
        assert state["errors"] == []
        assert state["evidence_by_dimension"] == {}

    def test_initial_state_creation(self):
        state: AgentState = {
            "user_query": "Monzo due diligence",
            "iteration_count": 1,
            "errors": [],
            "evidence_by_dimension": {},
        }
        assert state["user_query"] == "Monzo due diligence"
        assert state["iteration_count"] == 1

    def test_partial_update_pattern(self):
        """Agent nodes return dicts of partial updates -- validate the pattern."""
        state: AgentState = {
            "user_query": "test",
            "iteration_count": 1,
            "errors": [],
            "evidence_by_dimension": {},
        }
        # Simulate what an agent returns
        update = {"iteration_count": 2}
        state.update(update)
        assert state["iteration_count"] == 2
        assert state["user_query"] == "test"  # unchanged

    def test_errors_field_accumulates(self):
        state: AgentState = {
            "user_query": "test",
            "iteration_count": 1,
            "errors": [],
            "evidence_by_dimension": {},
        }
        state["errors"].append({"agent": "fca_agent", "error": "timeout"})
        assert len(state["errors"]) == 1
