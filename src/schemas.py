"""
schemas.py
All Pydantic input/output models for the agentic underwriting intelligence framework.

Rule: every agent output must include a confidence: float field (0.0-1.0).
      every evidence item must include source: str and retrieved_at: datetime.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# -- Enums -------------------------------------------------------------------

class ResearchDimension(str, Enum):
    COMPANY_PROFILE      = "company_profile"
    OFFICERS             = "officers"
    FILING_HISTORY       = "filing_history"
    REGULATORY_STATUS    = "regulatory_status"
    NEWS_SIGNALS         = "news_signals"
    WEB_EVIDENCE         = "web_evidence"
    FRAUD_SIGNALS        = "fraud_signals"        # director disqualification, phoenix pattern, sanctions
    BENEFICIAL_OWNERSHIP = "beneficial_ownership" # PSC register, offshore structures, PEP flags


class ConnectorName(str, Enum):
    COMPANIES_HOUSE = "companies_house"
    FCA_REGISTER = "fca_register"
    BRAVE_SEARCH = "brave_search"
    WEB_EVIDENCE = "web_evidence"
    COMPANIES_HOUSE_FRAUD = "companies_house_fraud"
    OPEN_SANCTIONS = "open_sanctions"
    COMPANIES_HOUSE_PSC = "companies_house_psc"


class NewsSignalCategory(str, Enum):
    """Categorised news signal types for insurance underwriting."""
    REGULATORY          = "regulatory"          # FCA/PRA enforcement, censures, investigations
    FINANCIAL_DISTRESS  = "financial_distress"  # profit warnings, refinancing, CVA threat
    LITIGATION          = "litigation"          # lawsuits against, class actions, tribunal rulings
    OPERATIONAL_INCIDENT = "operational_incident" # data breach, system failure, recall
    GOVERNANCE_CHANGE   = "governance_change"   # CFO departure, board dispute, activist shareholder
    FRAUD_ALLEGATION    = "fraud_allegation"    # whistleblower, SFO, fraud investigation
    MA_ACTIVITY         = "ma_activity"         # acquisition, merger, change of control


class EvidenceQuality(str, Enum):
    HIGH    = "high"
    MEDIUM  = "medium"
    LOW     = "low"
    MISSING = "missing"


class RiskLevel(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


# -- Evidence primitives -----------------------------------------------------

class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    dimension: ResearchDimension
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)
    raw_data: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    quality: EvidenceQuality = EvidenceQuality.MEDIUM
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


# -- Agent output schemas ----------------------------------------------------

class QueryUnderstandingOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    original_query: str
    company_name: str
    research_objective: str
    requested_dimensions: list[ResearchDimension]
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class EntityResolutionOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    company_name: str
    company_number: str | None = None
    fca_firm_reference: str | None = None
    resolution_method: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: str = ""


class ResearchPlanOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dimensions_to_investigate: list[ResearchDimension]
    rationale: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SourceSelectionOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dimension_to_connector: dict[ResearchDimension, ConnectorName]
    rationale: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class EvidenceCritiqueOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dimension_quality: dict[str, EvidenceQuality]
    overall_quality_score: float = Field(ge=0.0, le=1.0)
    weak_dimensions: list[str] = Field(default_factory=list)
    critique_notes: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class GapDetectionOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    missing_dimensions: list[ResearchDimension]
    partially_covered: list[ResearchDimension]
    gap_score: float = Field(ge=0.0, le=1.0)  # 0 = no gaps, 1 = all missing
    follow_up_needed: bool
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class FollowUpPlanOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    should_iterate: bool
    dimensions_to_retry: list[ResearchDimension]
    additional_queries: list[str] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Contradiction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dimension: ResearchDimension
    source_a: str
    source_b: str
    description: str
    severity: RiskLevel = RiskLevel.MEDIUM


class ContradictionDetectionOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contradictions: list[Contradiction] = Field(default_factory=list)
    contradiction_count: int = 0
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class RiskFlag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str
    description: str
    level: RiskLevel
    source: str
    evidence_snippet: str = ""


# -- Insurance-specific models -----------------------------------------------

class RiskMatrix(BaseModel):
    """Five-dimensional risk breakdown for underwriting assessment."""
    model_config = ConfigDict(extra="ignore")

    financial_risk:    RiskLevel = RiskLevel.LOW
    regulatory_risk:   RiskLevel = RiskLevel.LOW
    operational_risk:  RiskLevel = RiskLevel.LOW
    governance_risk:   RiskLevel = RiskLevel.LOW
    fraud_risk:        RiskLevel = RiskLevel.LOW

    @field_validator(
        "financial_risk", "regulatory_risk", "operational_risk",
        "governance_risk", "fraud_risk",
        mode="before",
    )
    @classmethod
    def coerce_risk_level(cls, v: object) -> object:
        """Coerce any unrecognised value (e.g. 'unknown') to RiskLevel.LOW.

        The LLM occasionally outputs 'unknown' when evidence is missing.
        This validator catches it before Pydantic raises a validation error,
        keeping the tool call valid and defaulting to the safest/lowest signal.
        """
        valid = {r.value for r in RiskLevel}
        if isinstance(v, str) and v.lower() not in valid:
            return RiskLevel.LOW
        return v


class NewsCandidate(BaseModel):
    """Source material awaiting LLM classification."""
    model_config = ConfigDict(extra="ignore")

    candidate_id: str
    headline: str
    source_url: str = ""
    date: str = ""
    summary: str = ""
    search_category: NewsSignalCategory


class NewsClassification(BaseModel):
    """LLM decision for one retrieved news candidate."""
    model_config = ConfigDict(extra="ignore")

    candidate_id: str
    category: NewsSignalCategory
    severity: RiskLevel
    rationale: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class NewsClassificationOutput(BaseModel):
    """Batched output from NewsClassificationAgent."""
    model_config = ConfigDict(extra="ignore")

    classifications: list[NewsClassification]
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class NewsSignal(BaseModel):
    """A source-preserving news signal with an LLM classification."""
    model_config = ConfigDict(extra="ignore")

    category: NewsSignalCategory
    headline: str
    source_url: str = ""
    date: str = ""
    severity: RiskLevel
    summary: str = ""
    classification_rationale: str = ""
    classification_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class UnderwritingEvidenceFacts(BaseModel):
    """Authoritative structured facts projected from retrieved evidence."""
    model_config = ConfigDict(extra="ignore")

    disqualified_officers: list[str] = Field(default_factory=list)
    phoenix_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    phoenix_evidence: list[str] = Field(default_factory=list)
    sanctions_hits: list[str] = Field(default_factory=list)
    enforcement_actions: list[str] = Field(default_factory=list)
    psc_risk_flags: list[str] = Field(default_factory=list)


# -- Primary output schemas ---------------------------------------------------

class DueDiligenceBrief(BaseModel):
    """Base structured output -- kept for backward compatibility with existing tests."""
    model_config = ConfigDict(extra="ignore")

    company_name: str
    company_number: str | None = None
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    # Dimensional summaries
    company_status_summary: str = ""
    officers_summary: str = ""
    filing_activity_summary: str = ""
    regulatory_summary: str = ""
    news_signals_summary: str = ""

    # Synthesis
    key_risks: list[RiskFlag] = Field(default_factory=list)
    overall_risk_level: RiskLevel = RiskLevel.MEDIUM
    contradictions_detected: list[Contradiction] = Field(default_factory=list)

    # Evidence trace
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    dimensions_covered: list[ResearchDimension] = Field(default_factory=list)
    dimensions_missing: list[ResearchDimension] = Field(default_factory=list)

    # Confidence
    overall_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence_rationale: str = ""

    # Research metadata
    iterations_performed: int = 1


class UnderwritingAssessment(DueDiligenceBrief):
    """Insurance underwriting pre-screening assessment.

    Extends DueDiligenceBrief with fraud intelligence, beneficial ownership
    flags, categorised news signals, and insurance-specific outputs
    (referral triggers, coverage exclusions, premium loading indicators).

    This is the primary API output schema.
    """
    model_config = ConfigDict(extra="ignore")

    # Additional dimensional summaries (beyond base brief)
    fraud_signals_summary: str = ""
    beneficial_ownership_summary: str = ""
    web_evidence_summary: str = ""

    # Five-dimensional risk matrix
    risk_matrix: RiskMatrix = Field(default_factory=RiskMatrix)

    # Fraud intelligence
    disqualified_officers: list[str] = Field(default_factory=list)
    phoenix_risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    phoenix_evidence: list[str] = Field(default_factory=list)
    sanctions_hits: list[str] = Field(default_factory=list)
    enforcement_actions: list[str] = Field(default_factory=list)

    # Beneficial ownership
    psc_risk_flags: list[str] = Field(default_factory=list)

    # Categorised news signals
    news_signals: list[NewsSignal] = Field(default_factory=list)

    # Insurance-specific outputs
    referral_triggers: list[str] = Field(default_factory=list)
    coverage_exclusions: list[str] = Field(default_factory=list)
    loading_indicators: list[str] = Field(default_factory=list)
    decline_indicators: list[str] = Field(default_factory=list)
    applicable_lines: list[str] = Field(default_factory=list)


# -- LLM intermediate output for synthesis -----------------------------------

class SynthesisAnalysis(BaseModel):
    """Analytical content produced by the LLM synthesis step.

    The synthesis_agent uses this as the structured LLM output, then assembles
    the full UnderwritingAssessment by merging it with state metadata.

    Optional fields default to empty or low-risk values when the model omits
    them.
    """
    model_config = ConfigDict(extra="ignore")

    # Dimensional summaries
    company_status_summary: str = Field(description="Company registration status, type, incorporation date, address.")
    officers_summary: str = Field(description="Current and recent officers, notable appointments or resignations, governance concerns.")
    filing_activity_summary: str = Field(description="Filing history -- frequency, types, overdue or unusual filings.")
    regulatory_summary: str = Field(description="FCA authorisation status, permissions, disciplinary history.")
    news_signals_summary: str = Field(description="Categorised news signals -- regulatory, distress, litigation, operational, governance, fraud, M&A.")
    fraud_signals_summary: str = Field(default="", description="Director disqualification findings, phoenix pattern indicators, sanctions screening results.")
    beneficial_ownership_summary: str = Field(default="", description="PSC register findings, offshore structures, PEP connections, ownership risk flags.")
    web_evidence_summary: str = Field(default="", description="Open-web supplementary evidence supporting or contradicting other dimensions.")

    # Risk assessment
    key_risks: list[RiskFlag] = Field(default_factory=list, description="Named risk flags with category, description, level, source, and evidence snippet.")
    overall_risk_level: RiskLevel = Field(default=RiskLevel.MEDIUM, description="Aggregate risk level: low, medium, high, or critical.")
    risk_matrix: RiskMatrix = Field(default_factory=RiskMatrix, description="Five-dimensional risk breakdown: financial, regulatory, operational, governance, fraud.")

    # Insurance-specific outputs
    referral_triggers: list[str] = Field(default_factory=list, description="Specific findings that must be referred to a human underwriter.")
    coverage_exclusions: list[str] = Field(default_factory=list, description="Recommended policy exclusions based on identified risks.")
    loading_indicators: list[str] = Field(default_factory=list, description="Reasons to apply premium loading.")
    decline_indicators: list[str] = Field(default_factory=list, description="Automatic decline signals -- sanctions hits, active disqualification, critical fraud flags.")
    applicable_lines: list[str] = Field(default_factory=list, description="Lines of business this assessment is most relevant to (e.g. D&O, FI, Cyber, Trade Credit).")

    # Confidence
    overall_confidence: float = Field(description="Confidence in the assessment (0.0-1.0) based on evidence quality and coverage.")
    confidence_rationale: str = Field(description="One or two sentences explaining the confidence score.")
