"""
tests/test_director_fraud_propagation.py
-----------------------------------------
End-to-end proof that a director's fraud history drives a HIGH/CRITICAL
assessment and reaches the final brief -- exercising the REAL FraudRetriever
logic and the REAL deterministic evidence projection (no LLM, no network).

Scenario (modelled on the public Companies House disqualified-officers register
+ Insolvency Service CDDA disqualifications for fraudulent / phoenix trading):

    "Northgate Trading Ltd" has a sitting director who:
      * appears on the disqualified-officers register (fraudulent trading), and
      * shows a phoenix pattern: 4 of his 5 prior companies dissolved/liquidated,
        the most recent within the last 5 years.

We assert that this single bad director:
      * produces a HIGH-quality fraud EvidenceItem (not weak/low),
      * yields a CRITICAL phoenix risk score (> 0.7),
      * is copied verbatim into the final UnderwritingAssessment via the
        deterministic projection, where it populates decline-driving fields.
"""

from __future__ import annotations

from src.retrievers.fraud_retriever import FraudRetriever, _PHOENIX_CRITICAL
from src.evidence_facts import extract_underwriting_evidence_facts
from src.schemas import (
    EvidenceQuality,
    ResearchDimension,
    UnderwritingAssessment,
)

DISQUALIFIED_DIRECTOR = "JOHN MICHAEL CARVER"
TARGET_COMPANY_NUMBER = "09988776"
TARGET_COMPANY_NAME = "NORTHGATE TRADING LTD"


class _FakeFraudConnector:
    """Returns Companies-House-shaped data for a disqualified phoenix director."""

    use_mock = False

    def fetch_officers_with_ids(self, company_number: str):
        return [
            {
                "name": DISQUALIFIED_DIRECTOR,
                "officer_role": "director",
                "appointed_on": "2023-02-01",
                "resigned_on": None,
                "officer_id": "abcDEF123officerID",
            },
            {
                "name": "SARAH ELLEN WHITMORE",
                "officer_role": "director",
                "appointed_on": "2023-02-01",
                "resigned_on": None,
                "officer_id": "cleanOfficerID999",
            },
        ]

    def check_disqualification_by_id(self, officer_id: str):
        # Non-empty dict == on the disqualified register. Shaped like the real
        # GET /disqualified-officers/natural/{id} payload.
        if officer_id == "abcDEF123officerID":
            return {
                "disqualifications": [
                    {
                        "disqualified_from": "2022-09-15",
                        "disqualified_until": "2034-09-15",
                        "reason": {
                            "description_identifier": "order-or-undertaking",
                            "act": "company-directors-disqualification-act-1986",
                            "article": "6",
                        },
                        "company_names": ["PHOENIX A LTD", "PHOENIX B LTD"],
                    }
                ]
            }
        return {}  # 404 / clean

    def search_disqualified_by_name(self, name: str):
        return []

    def fetch_appointment_history(self, officer_id: str):
        if officer_id != "abcDEF123officerID":
            return []
        # 4 of 5 companies failed, one within the last 5 years -> critical phoenix
        return [
            {"company_name": "PHOENIX A LTD", "company_number": "07111111",
             "company_status": "dissolved", "appointed_on": "2016-01-01",
             "resigned_on": "2018-06-01"},
            {"company_name": "PHOENIX B LTD", "company_number": "08222222",
             "company_status": "liquidation", "appointed_on": "2019-03-01",
             "resigned_on": "2021-02-01"},
            {"company_name": "PHOENIX C LTD", "company_number": "09333333",
             "company_status": "dissolved", "appointed_on": "2021-05-01",
             "resigned_on": "2023-01-01"},
            {"company_name": "PHOENIX D LTD", "company_number": "10444444",
             "company_status": "administration", "appointed_on": "2023-07-01",
             "resigned_on": None},
            {"company_name": TARGET_COMPANY_NAME, "company_number": TARGET_COMPANY_NUMBER,
             "company_status": "active", "appointed_on": "2023-02-01",
             "resigned_on": None},
        ]


class _FakeSanctionsConnector:
    def screen_entities(self, company_name: str, officer_names):
        return {"hits": {}, "screened_count": 1 + len(officer_names), "total_hits": 0}


def _run_real_fraud_retriever():
    retriever = FraudRetriever()
    retriever.connector = _FakeFraudConnector()      # real logic, fake I/O
    retriever._sanctions = _FakeSanctionsConnector()
    return retriever.retrieve(TARGET_COMPANY_NUMBER, TARGET_COMPANY_NAME)


def test_disqualified_director_produces_high_quality_critical_fraud_signal():
    item = _run_real_fraud_retriever()

    assert item.dimension == ResearchDimension.FRAUD_SIGNALS
    # A disqualified director must NOT be reported as weak/low evidence.
    assert item.quality == EvidenceQuality.HIGH
    assert item.confidence >= 0.9
    assert DISQUALIFIED_DIRECTOR in item.raw_data["disqualified_officers"]
    # Phoenix pattern reaches CRITICAL band.
    assert item.raw_data["phoenix_risk_score"] >= _PHOENIX_CRITICAL


def test_director_fraud_reaches_final_underwriting_assessment():
    item = _run_real_fraud_retriever()
    evidence = {ResearchDimension.FRAUD_SIGNALS.value: item}

    facts = extract_underwriting_evidence_facts(evidence)
    assert DISQUALIFIED_DIRECTOR in facts.disqualified_officers
    assert facts.phoenix_risk_score >= _PHOENIX_CRITICAL

    # The deterministic projection is what guarantees the fact survives synthesis:
    brief = UnderwritingAssessment(
        company_name=TARGET_COMPANY_NAME,
        disqualified_officers=facts.disqualified_officers,
        phoenix_risk_score=facts.phoenix_risk_score,
        phoenix_evidence=facts.phoenix_evidence,
        sanctions_hits=facts.sanctions_hits,
        enforcement_actions=facts.enforcement_actions,
        psc_risk_flags=facts.psc_risk_flags,
    )
    assert brief.disqualified_officers == [DISQUALIFIED_DIRECTOR]
    assert brief.phoenix_risk_score >= _PHOENIX_CRITICAL
