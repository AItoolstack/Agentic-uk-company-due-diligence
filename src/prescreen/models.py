"""
prescreen/models.py
The contract for an underwriter pre-screen.

Two ideas carry the whole design:

1. Every check ends in exactly one of four states. "Couldn't check" is its own
   state, never folded into "clear", and the report counts it.
2. The route (note / refer / outside appetite) is decided by written rules over
   those states, not by a model. The model only reads news and writes a summary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CheckStatus(str, Enum):
    CLEAR = "clear"                  # looked, found nothing that matters
    FLAG = "flag"                    # looked, found something
    COULDNT_CHECK = "couldnt_check"  # tried and failed, or no key / paid source
    NOT_APPLICABLE = "not_applicable"


class Route(str, Enum):
    """What the tool recommends. The underwriter decides."""
    NOTE = "note"                          # carry on, read the notes
    REFER = "refer"                        # needs a senior underwriter or a question to the broker
    OUTSIDE_APPETITE = "outside_appetite"  # decline unless overridden with reasons

    @property
    def rank(self) -> int:
        return {Route.NOTE: 0, Route.REFER: 1, Route.OUTSIDE_APPETITE: 2}[self]


class SourceRef(BaseModel):
    label: str
    url: str = ""
    retrieved_at: datetime = Field(default_factory=utcnow)


class Finding(BaseModel):
    """One thing a check found. `route` is what this finding alone would mean."""
    rule_id: str
    route: Route
    text: str
    detail: dict[str, Any] = Field(default_factory=dict)
    sources: list[SourceRef] = Field(default_factory=list)


class CheckResult(BaseModel):
    check_id: str
    title: str
    status: CheckStatus
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    # Why a check couldn't run, in words an underwriter can act on.
    not_checked_reason: str = ""
    # Critical checks: if these can't run, the case can't be "note".
    critical: bool = False

    @property
    def worst_route(self) -> Route | None:
        if not self.findings:
            return None
        return max((f.route for f in self.findings), key=lambda r: r.rank)


class Candidate(BaseModel):
    company_number: str
    name: str
    status: str = ""
    date_of_creation: str = ""
    address: str = ""
    exact_name_match: bool = False


class IdentityResult(BaseModel):
    """The run only continues when this is `resolved`."""
    resolved: bool
    company_number: str = ""
    company_name: str = ""
    method: str = ""  # "number" | "unique_exact_name" | "user_choice"
    candidates: list[Candidate] = Field(default_factory=list)
    message: str = ""


class MediaItem(BaseModel):
    title: str
    url: str
    published: str = ""
    source: str = ""
    about_this_company: str = "unsure"  # yes | no | unsure
    category: str = ""
    severity: str = "low"               # low | medium | high
    reason: str = ""


class PreScreenRequest(BaseModel):
    company_number: str = Field(..., min_length=6, max_length=10)
    declared_trade: str = Field(default="", max_length=300)
    expects_fca_authorisation: bool = False


class PreScreenReport(BaseModel):
    report_id: str
    created_at: datetime = Field(default_factory=utcnow)
    rules_version: str
    company_number: str
    company_name: str
    request: PreScreenRequest
    route: Route
    route_reasons: list[str]
    checks: list[CheckResult]
    couldnt_check: list[str]
    media: list[MediaItem] = Field(default_factory=list)
    summary: str = ""
    summary_by: str = "rules"  # "rules" or the model name
    llm_calls: int = 0
    elapsed_seconds: float = 0.0
    decision: dict[str, Any] | None = None


class DecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(proceed|refer|decline)$")
    reason: str = Field(..., min_length=5, max_length=2000)
    underwriter: str = Field(default="", max_length=120)
    overrides_route: bool = False
