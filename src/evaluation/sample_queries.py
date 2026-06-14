"""
evaluation/sample_queries.py
-----------------------------
Canonical sample queries for evaluating the research agent.

Usage:
    from src.evaluation.sample_queries import SAMPLE_QUERIES
    query = SAMPLE_QUERIES[0]["query"]

Each entry includes:
  - query:           the raw user query string
  - expected_company: canonical company name for entity resolution validation
  - expected_dimensions: dimensions that MUST appear in the output
  - min_confidence:  minimum acceptable overall_confidence in the brief
"""

from __future__ import annotations

from src.schemas import ResearchDimension

SAMPLE_QUERIES: list[dict] = [
    {
        "id": "monzo_full",
        "query": (
            "Create a due diligence and risk intelligence brief for Monzo Bank Limited. "
            "Include company status, officers, filing activity, regulatory position, "
            "recent news signals, key risks, and confidence level."
        ),
        "expected_company": "MONZO BANK LIMITED",
        "expected_company_number": "09446231",
        "expected_dimensions": [
            ResearchDimension.COMPANY_PROFILE,
            ResearchDimension.OFFICERS,
            ResearchDimension.FILING_HISTORY,
            ResearchDimension.REGULATORY_STATUS,
            ResearchDimension.NEWS_SIGNALS,
        ],
        "min_confidence": 0.6,
    },
    {
        "id": "revolut_minimal",
        "query": "What is Revolut's regulatory status in the UK?",
        "expected_company": "REVOLUT LTD",
        "expected_company_number": None,  # resolve at runtime
        "expected_dimensions": [
            ResearchDimension.REGULATORY_STATUS,
        ],
        "min_confidence": 0.5,
    },
    {
        "id": "starling_officers",
        "query": "Who are the current directors of Starling Bank Limited?",
        "expected_company": "STARLING BANK LIMITED",
        "expected_company_number": None,
        "expected_dimensions": [
            ResearchDimension.OFFICERS,
        ],
        "min_confidence": 0.7,
    },
]
