"""Project classified news evidence into the final assessment."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from src.schemas import EvidenceItem, NewsSignal, ResearchDimension
from src.tracing import tracer


def extract_classified_news_signals(
    evidence: Mapping[str, EvidenceItem],
) -> list[NewsSignal]:
    """Return only successfully classified, schema-valid news signals."""
    item = evidence.get(ResearchDimension.NEWS_SIGNALS.value)
    if item is None or item.raw_data.get("classification_status") != "completed":
        return []

    signals: list[NewsSignal] = []
    for raw_signal in item.raw_data.get("signals", []):
        try:
            signals.append(NewsSignal.model_validate(raw_signal))
        except (TypeError, ValidationError) as error:
            tracer.log_error("NewsEvidenceProjection", error)
    return signals
