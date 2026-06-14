"""Classify retrieved news candidates in one structured LLM call.

Reads:  state["evidence_by_dimension"], state["entity_resolution"],
        state["collected_dimensions_this_pass"]
Writes: state["news_classification"], state["evidence_by_dimension"]
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.llm import get_llm
from src.prompts import NEWS_CLASSIFICATION_SYSTEM, NEWS_CLASSIFICATION_USER
from src.schemas import (
    EvidenceItem,
    EvidenceQuality,
    NewsCandidate,
    NewsClassification,
    NewsClassificationOutput,
    NewsSignal,
    ResearchDimension,
    RiskLevel,
)
from src.state import AgentState
from src.tracing import tracer

_SEVERITY_ORDER: dict[RiskLevel, int] = {
    RiskLevel.CRITICAL: 0,
    RiskLevel.HIGH: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.LOW: 3,
}


def news_classifier_agent(state: AgentState) -> dict:
    """Classify all newly retrieved news candidates as a typed batch."""
    tracer.log_agent_start("NewsClassificationAgent", state)

    evidence = state.get("evidence_by_dimension", {})
    news_key = ResearchDimension.NEWS_SIGNALS.value
    item = evidence.get(news_key)
    empty_output = NewsClassificationOutput(
        classifications=[],
        confidence=1.0,
    )
    if item is None:
        tracer.log_agent_end("NewsClassificationAgent", empty_output)
        return {"news_classification": empty_output}

    raw_data = dict(item.raw_data)
    collected = state.get("collected_dimensions_this_pass", [])
    if (
        collected
        and news_key not in collected
        and raw_data.get("classification_status") == "completed"
    ):
        prior = state.get("news_classification", empty_output)
        tracer.log_agent_end("NewsClassificationAgent", prior)
        return {"news_classification": prior}

    try:
        candidates = [
            NewsCandidate.model_validate(candidate)
            for candidate in raw_data.get("candidates", [])
        ]
    except Exception as error:
        return _classification_failure(
            state=state,
            evidence=evidence,
            item=item,
            raw_data=raw_data,
            candidate_count=0,
            error=error,
        )
    if not candidates:
        updated_item = item.model_copy(
            update={
                "raw_data": {
                    **raw_data,
                    "signals": [],
                    "classification_status": "no_candidates",
                }
            }
        )
        tracer.log_agent_end("NewsClassificationAgent", empty_output)
        return {
            "news_classification": empty_output,
            "evidence_by_dimension": {
                **evidence,
                news_key: updated_item,
            },
        }

    er = state.get("entity_resolution")
    company_name = er.company_name if er else "Unknown"
    llm = get_llm(tier="fast")
    structured = llm.with_structured_output(NewsClassificationOutput)
    messages = [
        SystemMessage(content=NEWS_CLASSIFICATION_SYSTEM),
        HumanMessage(
            content=NEWS_CLASSIFICATION_USER.format(
                company_name=company_name,
                candidates_json=json.dumps(
                    [
                        candidate.model_dump(mode="json")
                        for candidate in candidates
                    ],
                    indent=2,
                ),
            )
        ),
    ]

    try:
        output: NewsClassificationOutput = structured.invoke(messages)
        classifications = _validate_classifications(candidates, output)
        updated_item = _apply_classifications(item, candidates, classifications, output)
        tracer.log_agent_end("NewsClassificationAgent", output)
        return {
            "news_classification": output,
            "evidence_by_dimension": {
                **evidence,
                news_key: updated_item,
            },
        }
    except Exception as error:
        return _classification_failure(
            state=state,
            evidence=evidence,
            item=item,
            raw_data=raw_data,
            candidate_count=len(candidates),
            error=error,
        )


def _validate_classifications(
    candidates: list[NewsCandidate],
    output: NewsClassificationOutput,
) -> dict[str, NewsClassification]:
    expected_ids = {candidate.candidate_id for candidate in candidates}
    classifications: dict[str, NewsClassification] = {}
    for classification in output.classifications:
        if classification.candidate_id in classifications:
            raise ValueError(
                f"Duplicate news classification ID: "
                f"{classification.candidate_id}"
            )
        classifications[classification.candidate_id] = classification

    actual_ids = set(classifications)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids - actual_ids)
        unexpected = sorted(actual_ids - expected_ids)
        raise ValueError(
            f"News classification IDs did not match candidates. "
            f"Missing={missing}, unexpected={unexpected}"
        )
    return classifications


def _classification_failure(
    state: AgentState,
    evidence: dict[str, EvidenceItem],
    item: EvidenceItem,
    raw_data: dict[str, Any],
    candidate_count: int,
    error: Exception,
) -> dict[str, Any]:
    tracer.log_error("NewsClassificationAgent", error)
    failed_item = item.model_copy(
        update={
            "raw_data": {
                **raw_data,
                "signals": [],
                "classification_status": "failed",
                "classification_error": str(error),
            },
            "summary": (
                f"Retrieved {candidate_count} news candidates, but "
                "classification failed."
            ),
            "quality": EvidenceQuality.LOW,
            "confidence": min(item.confidence, 0.3),
        }
    )
    errors = list(state.get("errors", []))
    errors.append({"agent": "news_classifier", "error": str(error)})
    return {
        "news_classification": NewsClassificationOutput(
            classifications=[],
            confidence=0.0,
        ),
        "evidence_by_dimension": {
            **evidence,
            ResearchDimension.NEWS_SIGNALS.value: failed_item,
        },
        "errors": errors,
    }


def _apply_classifications(
    item: EvidenceItem,
    candidates: list[NewsCandidate],
    classifications: dict[str, NewsClassification],
    output: NewsClassificationOutput,
) -> EvidenceItem:
    signals = [
        NewsSignal(
            category=classifications[candidate.candidate_id].category,
            headline=candidate.headline,
            source_url=candidate.source_url,
            date=candidate.date,
            severity=classifications[candidate.candidate_id].severity,
            summary=candidate.summary,
            classification_rationale=(
                classifications[candidate.candidate_id].rationale
            ),
            classification_confidence=(
                classifications[candidate.candidate_id].confidence
            ),
        )
        for candidate in candidates
    ]
    signals.sort(
        key=lambda signal: (
            _SEVERITY_ORDER[signal.severity],
            signal.headline.lower(),
        )
    )

    high_count = sum(
        signal.severity in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        for signal in signals
    )
    medium_count = sum(
        signal.severity == RiskLevel.MEDIUM
        for signal in signals
    )
    by_category: dict[str, list[str]] = {}
    for signal in signals:
        by_category.setdefault(signal.category.value, []).append(
            signal.headline
        )
    top_signals = "; ".join(
        f"[{signal.severity.value}] {signal.headline}"
        for signal in signals[:3]
    )

    raw_data = dict(item.raw_data)
    raw_data.pop("classification_error", None)
    raw_data.update({
        "signals": [signal.model_dump(mode="json") for signal in signals],
        "by_category": by_category,
        "high_severity_count": high_count,
        "medium_severity_count": medium_count,
        "classification_status": "completed",
        "classification_confidence": output.confidence,
    })
    return item.model_copy(
        update={
            "raw_data": raw_data,
            "summary": (
                f"Classified {len(signals)} news signals | "
                f"High/critical: {high_count} | Medium: {medium_count}"
                + (f" | Top: {top_signals}" if top_signals else "")
            ),
            "confidence": min(item.confidence, output.confidence),
        }
    )
