"""
evaluation/trace_validator.py
------------------------------
Validates that a research run produced a complete and structurally sound
DueDiligenceBrief matching the expectations in a sample query.

Usage:
    from src.evaluation.trace_validator import validate_brief
    result = validate_brief(brief, sample_query)
    assert result.passed, result.failures

This is not an LLM-as-judge evaluation -- it validates structural correctness:
  - Required dimensions are covered
  - Overall confidence meets the minimum threshold
  - Evidence items are present
  - No required fields are empty
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.schemas import DueDiligenceBrief, ResearchDimension


@dataclass
class ValidationResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_brief(
    brief: DueDiligenceBrief,
    sample: dict,
) -> ValidationResult:
    """Validate a DueDiligenceBrief against a sample query expectation.

    Args:
        brief: The produced DueDiligenceBrief.
        sample: A dict from SAMPLE_QUERIES.

    Returns:
        ValidationResult with pass/fail status and failure messages.
    """
    failures: list[str] = []
    warnings: list[str] = []

    # 1. Confidence threshold
    min_conf = sample.get("min_confidence", 0.5)
    if brief.overall_confidence < min_conf:
        failures.append(
            f"Confidence {brief.overall_confidence:.2f} < required {min_conf:.2f}"
        )

    # 2. Required dimensions covered
    required: list[ResearchDimension] = sample.get("expected_dimensions", [])
    for dim in required:
        if dim not in brief.dimensions_covered:
            failures.append(f"Required dimension not covered: {dim.value}")

    # 3. Evidence items present
    if not brief.evidence_items:
        failures.append("No evidence items in brief")

    # 4. Key fields non-empty
    if not brief.company_name:
        failures.append("company_name is empty")
    if not brief.key_risks:
        warnings.append("No key risks identified -- may indicate weak synthesis")

    return ValidationResult(passed=len(failures) == 0, failures=failures, warnings=warnings)
