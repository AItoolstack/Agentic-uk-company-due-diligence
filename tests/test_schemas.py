"""
tests/test_schemas.py
---------------------
Validates that Pydantic schemas correctly parse sample data.
All tests must pass with USE_MOCK_DATA=true and no API keys.
"""

from __future__ import annotations

import pytest

from src.schemas import (
    ConnectorName,
    DueDiligenceBrief,
    EvidenceItem,
    EvidenceQuality,
    NewsClassificationOutput,
    QueryUnderstandingOutput,
    ResearchDimension,
    RiskLevel,
    SourceSelectionOutput,
)
from src.utils import mock_data_path, load_json_file


class TestEvidenceItem:
    def test_defaults(self):
        item = EvidenceItem(source="test", dimension=ResearchDimension.COMPANY_PROFILE)
        assert item.confidence == 0.5
        assert item.quality == EvidenceQuality.MEDIUM

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            EvidenceItem(
                source="test",
                dimension=ResearchDimension.OFFICERS,
                confidence=1.5,  # out of range
            )


class TestQueryUnderstandingOutput:
    def test_parse(self):
        data = {
            "original_query": "Tell me about Monzo",
            "company_name": "Monzo Bank Limited",
            "research_objective": "Produce a due diligence brief",
            "requested_dimensions": ["company_profile", "officers"],
            "confidence": 0.9,
        }
        output = QueryUnderstandingOutput.model_validate(data)
        assert output.company_name == "Monzo Bank Limited"
        assert ResearchDimension.COMPANY_PROFILE in output.requested_dimensions


class TestSourceSelectionOutput:
    def test_parses_typed_dimension_and_connector(self):
        output = SourceSelectionOutput.model_validate(
            {
                "dimension_to_connector": {
                    "news_signals": "brave_search",
                },
                "rationale": "Use open-web news search.",
                "confidence": 0.9,
            }
        )

        assert output.dimension_to_connector == {
            ResearchDimension.NEWS_SIGNALS: ConnectorName.BRAVE_SEARCH
        }

    def test_rejects_unknown_connector(self):
        with pytest.raises(Exception):
            SourceSelectionOutput.model_validate(
                {
                    "dimension_to_connector": {
                        "news_signals": "imaginary_news_source",
                    },
                    "rationale": "Invalid source.",
                    "confidence": 0.9,
                }
            )

    def test_rejects_unknown_dimension(self):
        with pytest.raises(Exception):
            SourceSelectionOutput.model_validate(
                {
                    "dimension_to_connector": {
                        "imaginary_dimension": "companies_house",
                    },
                    "rationale": "Invalid dimension.",
                    "confidence": 0.9,
                }
            )


class TestNewsClassificationOutput:
    def test_rejects_unknown_severity(self):
        with pytest.raises(Exception):
            NewsClassificationOutput.model_validate(
                {
                    "classifications": [
                        {
                            "candidate_id": "candidate-1",
                            "category": "regulatory",
                            "severity": "concerning",
                            "rationale": "Unsupported severity label.",
                            "confidence": 0.8,
                        }
                    ],
                    "confidence": 0.8,
                }
            )


class TestMockData:
    def test_companies_house_profile_loads(self):
        path = mock_data_path("companies_house_profile.json")
        data = load_json_file(path)
        assert data["company_number"] == "09446231"
        assert data["company_status"] == "active"

    def test_fca_register_loads(self):
        path = mock_data_path("fca_register.json")
        data = load_json_file(path)
        assert data["status"] == "Authorised"
        assert "permissions" in data

    def test_news_loads(self):
        path = mock_data_path("news.json")
        data = load_json_file(path)
        assert len(data["articles"]) > 0
