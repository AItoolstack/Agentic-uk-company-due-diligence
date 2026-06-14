"""Quality-aware merging for evidence collected across research passes."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from src.schemas import EvidenceItem, EvidenceQuality

_QUALITY_RANK: dict[EvidenceQuality, int] = {
    EvidenceQuality.MISSING: 0,
    EvidenceQuality.LOW: 1,
    EvidenceQuality.MEDIUM: 2,
    EvidenceQuality.HIGH: 3,
}


def merge_evidence_by_dimension(
    existing: Mapping[str, EvidenceItem],
    incoming: Mapping[str, EvidenceItem],
) -> dict[str, EvidenceItem]:
    """Merge a research pass without allowing weaker retries to erase evidence."""
    merged = dict(existing)
    for dimension, candidate in incoming.items():
        current = merged.get(dimension)
        if current is None or _prefer_candidate(candidate, current):
            merged[dimension] = candidate
    return merged


def _prefer_candidate(candidate: EvidenceItem, current: EvidenceItem) -> bool:
    candidate_rank = _QUALITY_RANK[candidate.quality]
    current_rank = _QUALITY_RANK[current.quality]
    if candidate_rank != current_rank:
        return candidate_rank > current_rank
    if candidate.confidence != current.confidence:
        return candidate.confidence > current.confidence
    return _as_utc(candidate.retrieved_at) >= _as_utc(current.retrieved_at)


def _as_utc(value: datetime) -> datetime:
    """Normalize mixed connector timestamps before comparing recency."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
