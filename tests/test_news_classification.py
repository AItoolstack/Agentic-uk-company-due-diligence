"""Tests for LLM-driven news classification and evidence handoff."""

from __future__ import annotations

from datetime import datetime

from src.schemas import (
    EntityResolutionOutput,
    EvidenceItem,
    EvidenceQuality,
    NewsCandidate,
    NewsClassification,
    NewsClassificationOutput,
    NewsSignalCategory,
    ResearchDimension,
    RiskLevel,
)


def _candidate() -> NewsCandidate:
    return NewsCandidate(
        candidate_id="candidate-1",
        headline="Example Limited reports material customer data incident",
        source_url="https://example.test/incident",
        date="2026-06-01",
        summary="The company notified the ICO after customer records were exposed.",
        search_category=NewsSignalCategory.OPERATIONAL_INCIDENT,
    )


def _news_evidence(candidate: NewsCandidate | None = None) -> EvidenceItem:
    candidates = [candidate.model_dump(mode="json")] if candidate else []
    return EvidenceItem(
        source="Brave Search",
        dimension=ResearchDimension.NEWS_SIGNALS,
        retrieved_at=datetime(2026, 6, 14, 10, 0),
        raw_data={
            "candidates": candidates,
            "signals": [],
            "classification_status": "pending" if candidates else "no_candidates",
        },
        summary="News candidates retrieved.",
        quality=EvidenceQuality.MEDIUM if candidates else EvidenceQuality.LOW,
        confidence=0.7 if candidates else 0.3,
    )


def test_news_retriever_returns_unclassified_mock_candidates() -> None:
    from src.connectors.brave_search import BraveSearchConnector
    from src.retrievers.news_retriever import NewsRetriever

    connector = BraveSearchConnector()
    connector.use_mock = True
    evidence = NewsRetriever(connector).retrieve(
        company_number="09446231",
        company_name="MONZO BANK LIMITED",
    )

    assert evidence.raw_data["classification_status"] == "pending"
    assert evidence.raw_data["signals"] == []
    assert evidence.raw_data["total_candidates"] == 5
    assert all(
        "severity" not in candidate
        for candidate in evidence.raw_data["candidates"]
    )


def test_news_classifier_batches_candidates_and_preserves_source_fields(
    monkeypatch,
) -> None:
    from src.agents import news_classifier as classifier_module

    candidate = _candidate()
    output = NewsClassificationOutput(
        classifications=[
            NewsClassification(
                candidate_id=candidate.candidate_id,
                category=NewsSignalCategory.OPERATIONAL_INCIDENT,
                severity=RiskLevel.HIGH,
                rationale="Confirmed customer-data exposure with regulator notice.",
                confidence=0.91,
            )
        ],
        confidence=0.88,
    )
    captured_messages: list[object] = []

    class FakeStructuredLLM:
        def invoke(self, messages):
            captured_messages.extend(messages)
            return output

    class FakeLLM:
        def with_structured_output(self, schema):
            assert schema is NewsClassificationOutput
            return FakeStructuredLLM()

    monkeypatch.setattr(
        classifier_module,
        "get_llm",
        lambda tier: FakeLLM(),
    )

    result = classifier_module.news_classifier_agent(
        {
            "entity_resolution": EntityResolutionOutput(
                company_name="EXAMPLE LIMITED",
                company_number="01234567",
                confidence=0.95,
            ),
            "evidence_by_dimension": {
                ResearchDimension.NEWS_SIGNALS.value:
                    _news_evidence(candidate)
            },
            "collected_dimensions_this_pass": [
                ResearchDimension.NEWS_SIGNALS.value
            ],
            "errors": [],
        }
    )

    news = result["evidence_by_dimension"][
        ResearchDimension.NEWS_SIGNALS.value
    ]
    signal = news.raw_data["signals"][0]
    assert signal["headline"] == candidate.headline
    assert signal["source_url"] == candidate.source_url
    assert signal["severity"] == RiskLevel.HIGH.value
    assert signal["classification_confidence"] == 0.91
    assert news.raw_data["classification_status"] == "completed"
    assert news.confidence == 0.7
    assert "[high] Example Limited reports material" in news.summary
    assert candidate.candidate_id in captured_messages[1].content


def test_news_classifier_rejects_incomplete_model_output(monkeypatch) -> None:
    from src.agents import news_classifier as classifier_module

    class FakeStructuredLLM:
        def invoke(self, messages):
            return NewsClassificationOutput(
                classifications=[],
                confidence=0.9,
            )

    class FakeLLM:
        def with_structured_output(self, schema):
            return FakeStructuredLLM()

    monkeypatch.setattr(
        classifier_module,
        "get_llm",
        lambda tier: FakeLLM(),
    )

    result = classifier_module.news_classifier_agent(
        {
            "evidence_by_dimension": {
                ResearchDimension.NEWS_SIGNALS.value:
                    _news_evidence(_candidate())
            },
            "collected_dimensions_this_pass": [
                ResearchDimension.NEWS_SIGNALS.value
            ],
            "errors": [],
        }
    )

    news = result["evidence_by_dimension"][
        ResearchDimension.NEWS_SIGNALS.value
    ]
    assert news.raw_data["classification_status"] == "failed"
    assert news.raw_data["signals"] == []
    assert news.quality == EvidenceQuality.LOW
    assert result["news_classification"].confidence == 0.0
    assert result["errors"][0]["agent"] == "news_classifier"


def test_news_classifier_skips_llm_when_no_candidates(monkeypatch) -> None:
    from src.agents import news_classifier as classifier_module

    def fail_if_called(tier):
        raise AssertionError("LLM should not be called without candidates")

    monkeypatch.setattr(classifier_module, "get_llm", fail_if_called)

    result = classifier_module.news_classifier_agent(
        {
            "evidence_by_dimension": {
                ResearchDimension.NEWS_SIGNALS.value: _news_evidence()
            },
            "errors": [],
        }
    )

    assert result["news_classification"].classifications == []
    assert result["news_classification"].confidence == 1.0


def test_news_classifier_handles_malformed_candidate_evidence() -> None:
    from src.agents.news_classifier import news_classifier_agent

    malformed = _news_evidence()
    malformed.raw_data = {
        "candidates": [{"headline": "Missing required fields"}],
        "classification_status": "pending",
    }

    result = news_classifier_agent(
        {
            "evidence_by_dimension": {
                ResearchDimension.NEWS_SIGNALS.value: malformed
            },
            "errors": [],
        }
    )

    news = result["evidence_by_dimension"][
        ResearchDimension.NEWS_SIGNALS.value
    ]
    assert news.raw_data["classification_status"] == "failed"
    assert news.quality == EvidenceQuality.LOW
    assert result["errors"][0]["agent"] == "news_classifier"


def test_news_classifier_progress_detail() -> None:
    from src.api.routes import _extract_node_detail

    detail = _extract_node_detail(
        "news_classifier",
        {
            "news_classification": NewsClassificationOutput(
                classifications=[
                    NewsClassification(
                        candidate_id="candidate-1",
                        category=NewsSignalCategory.REGULATORY,
                        severity=RiskLevel.MEDIUM,
                        rationale="Regulatory review remains unresolved.",
                        confidence=0.8,
                    )
                ],
                confidence=0.85,
            )
        },
    )

    assert detail == "Classified 1 news signal at 85% confidence"
