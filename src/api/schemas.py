"""
api/schemas.py
Pydantic request/response models for the FastAPI layer.

These are API-boundary types -- separate from the domain schemas in src/schemas.py.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ResearchRequest(BaseModel):
    """POST /research body."""

    query: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Natural-language due diligence research query.",
        examples=["Create a due diligence brief for Monzo Bank Limited"],
    )


class ResearchResponse(BaseModel):
    """Sync endpoint response: full brief + metadata."""

    brief: Optional[dict[str, Any]] = Field(
        default=None,
        description="Serialised DueDiligenceBrief, or null if synthesis failed.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Non-fatal agent errors accumulated during the run.",
    )
    elapsed_seconds: float = Field(default=0.0, description="Wall-clock run time.")


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.5.0"


class ChatRequest(BaseModel):
    """POST /research/chat body."""

    brief: dict[str, Any] = Field(
        description="Serialised brief from a previous research run."
    )
    question: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Follow-up question about the brief.",
    )


class ChatResponse(BaseModel):
    """POST /research/chat response."""

    answer: str = Field(description="LLM answer to the follow-up question.")
