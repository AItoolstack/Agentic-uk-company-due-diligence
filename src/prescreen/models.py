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

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CheckStatus(str, Enum):
    CLEAR = "clear"                  # looked, found nothing
    INFO = "info"                    # looked, found things worth reading that don't change the route
    FLAG = "flag"                    # looked, found something that changes the route
    COULDNT_CHECK = "couldnt_check"  # tried and failed, or no key / paid source
    NOT_APPLICABLE = "not_applicable"


class Route(str, Enum):
    """What the tool recommends. The underwriter decides."""
    NOTE = "note"                          # carry on, read the notes
    REFER = "refer"                        # needs a senior underwriter or a question to the broker
    OUTSIDE_APPETITE = "outside_appetite"  # decline unless overridden with reasons
    COMPLIANCE_HOLD = "compliance_hold"    # possible sanctions match confirmed by DOB: stop, compliance decides

    @property
    def rank(self) -> int:
        return {Route.NOTE: 0, Route.REFER: 1, Route.OUTSIDE_APPETITE: 2, Route.COMPLIANCE_HOLD: 3}[self]


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
    # Fraud indicators this finding is also evidence of (e.g. "phoenix"). Set by casefile.py.
    tags: list[str] = Field(default_factory=list)
    # For the evidence drawer. event_date: when it happened (ISO), not when it was reported. relationship: how
    # the person or entity relates to this company now. why_for_cover: set by casefile from the cover lines.
    event_date: str = ""
    relationship: str = ""
    why_for_cover: str = ""


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
    # Which underwriter question this check answers (casefile.GROUPS). Set by the engine.
    group: str = ""
    # True when the tool doesn't do this check at all (CCJs: paid data). Still couldnt_check, never clear,
    # but counted as "not covered by this tool", not as a source that failed (casefile.coverage).
    out_of_scope: bool = False

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
    about_this_company: str = "unsure"  # yes | related | no | unsure | not_reviewed
    entity_named: str = ""              # the legal entity the article names, as the model read it
    category: str = ""
    severity: str = "low"               # low | medium | high
    event_year: int | None = None       # when the reported event happened, if the article says (a 2017 fine reported in 2024)
    reason: str = ""


COVER_LINES = {
    "property": "Property",
    "liability": "Employers' / public liability",
    "professional_indemnity": "Professional indemnity",
    "management_liability": "D&O / management liability",
    "financial_institutions": "Financial institutions",
    "cyber": "Cyber",
    "motor_fleet": "Motor fleet",
}
FINANCIAL_LINES = {"professional_indemnity", "management_liability", "financial_institutions"}


class PreScreenRequest(BaseModel):
    company_number: str = Field(..., min_length=6, max_length=10)
    declared_trade: str = Field(default="", max_length=300)
    expects_fca_authorisation: bool = False
    cover_lines: list[str] = Field(default_factory=list, max_length=7)
    declared_turnover_gbp: int | None = Field(default=None, ge=0, le=10**12)
    declared_years_trading: int | None = Field(default=None, ge=0, le=300)
    declared_directors: str = Field(default="", max_length=600)  # names from the proposal, comma separated
    # A re-run from a case ("Re-run with this proposal"): the report it was made from. A new case, linked.
    rerun_of: str = Field(default="", pattern=r"^([0-9a-f]{16})?$")

    @field_validator("declared_turnover_gbp", mode="before")
    @classmethod
    def _turnover(cls, v: Any) -> Any:
        # Same rules as the page's "Read as £2,400,000": £2.4m, 750k, 2,400,000; anything else is refused.
        from src.prescreen.caseview import parse_money
        return parse_money(v)

    @field_validator("declared_years_trading", mode="before")
    @classmethod
    def _years(cls, v: Any) -> Any:
        from src.prescreen.caseview import parse_years
        return parse_years(v)

    @field_validator("company_number")
    @classmethod
    def _number(cls, v: str) -> str:
        n = re.sub(r"\s+", "", v or "").upper()
        if not re.fullmatch(r"[A-Z]{2}\d{6}|\d{6,8}", n):
            raise ValueError("Enter a Companies House number: 8 digits, or 2 letters and 6 digits (e.g. SC123456).")
        return n.zfill(8) if n.isdigit() else n

    @field_validator("cover_lines")
    @classmethod
    def _known_lines(cls, v: list[str]) -> list[str]:
        return [x for x in dict.fromkeys(v) if x in COVER_LINES]

    @property
    def financial_lines(self) -> bool:
        return bool(set(self.cover_lines) & FINANCIAL_LINES)


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
    llm_usage: dict[str, Any] = Field(default_factory=dict)  # tokens in/out and model, for cost
    elapsed_seconds: float = 0.0
    decision: dict[str, Any] | None = None
    company: dict[str, Any] = Field(default_factory=dict)  # identity card: what the underwriter confirms first
    broker_questions: list[dict[str, Any]] = Field(default_factory=list)
    proposal_vs_record: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)  # casefile.coverage: one count used everywhere
    # For the case page (caseview.py). None of these change the route.
    sources_read: list[dict[str, Any]] = Field(default_factory=list)  # each register read, with when and how
    news_view: dict[str, Any] = Field(default_factory=dict)           # news grouped into events and sections
    broker_email: dict[str, Any] = Field(default_factory=dict)        # built from broker_questions
    proposal_read: dict[str, Any] = Field(default_factory=dict)       # how the proposal fields were read
    rerun_of: str = ""


class DecisionRequest(BaseModel):
    # "compliance": referred to compliance, the only decision on a compliance hold.
    decision: str = Field(..., pattern="^(proceed|refer|decline|compliance)$")
    reason: str = Field(..., min_length=5, max_length=2000)
    underwriter: str = Field(default="", max_length=120)
    # Ignored: the server works out whether a decision overrides the route (caseview.check_decision).
    overrides_route: bool = False
    # Required: the underwriter matched the company number and registered address to the proposal.
    identity_confirmed: bool | None = None
