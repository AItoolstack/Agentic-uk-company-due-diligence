"""
Project authoritative structured facts from retrieved evidence.

This module does not assess risk. It copies factual connector outputs into the
final underwriting schema so the LLM can interpret them without becoming the
system of record.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from src.schemas import EvidenceItem, ResearchDimension, UnderwritingEvidenceFacts


def extract_underwriting_evidence_facts(
    evidence_by_dimension: Mapping[str, EvidenceItem],
) -> UnderwritingEvidenceFacts:
    """Extract final-schema facts from fraud, ownership, and FCA evidence."""
    fraud = _raw_data(evidence_by_dimension, ResearchDimension.FRAUD_SIGNALS)
    ownership = _raw_data(
        evidence_by_dimension,
        ResearchDimension.BENEFICIAL_OWNERSHIP,
    )
    regulatory = _raw_data(
        evidence_by_dimension,
        ResearchDimension.REGULATORY_STATUS,
    )

    sanctions_hits = _deduplicate(
        _string_list(fraud.get("sanctions_hits"))
        + _string_list(ownership.get("sanctions_hits"))
    )
    enforcement_actions = _deduplicate(
        _readable_list(regulatory.get("enforcement_actions"))
        + _readable_list(regulatory.get("disciplinary_history"))
        + _readable_list(fraud.get("enforcement_actions"))
    )

    return UnderwritingEvidenceFacts(
        disqualified_officers=_deduplicate(
            _string_list(fraud.get("disqualified_officers"))
        ),
        phoenix_risk_score=fraud.get("phoenix_risk_score", 0.0),
        phoenix_evidence=_deduplicate(
            _string_list(fraud.get("phoenix_evidence"))
        ),
        sanctions_hits=sanctions_hits,
        enforcement_actions=enforcement_actions,
        psc_risk_flags=_deduplicate(
            _string_list(ownership.get("psc_risk_flags"))
        ),
    )


def _raw_data(
    evidence_by_dimension: Mapping[str, EvidenceItem],
    dimension: ResearchDimension,
) -> dict[str, Any]:
    item = evidence_by_dimension.get(dimension.value)
    return item.raw_data if item else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _readable_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            output.append(item.strip())
        elif isinstance(item, dict):
            output.append(_format_record(item))
    return output


def _format_record(record: dict[str, Any]) -> str:
    description = (
        record.get("description")
        or record.get("action")
        or record.get("type")
        or record.get("title")
    )
    reference = (
        record.get("reference")
        or record.get("action_reference")
        or record.get("requirement_reference")
    )
    action_date = (
        record.get("date")
        or record.get("effective_date")
        or record.get("published_at")
    )

    parts = [str(value).strip() for value in (description, reference, action_date) if value]
    if parts:
        return " | ".join(parts)
    return json.dumps(record, sort_keys=True, default=str)


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
