#!/usr/bin/env python3
"""
scripts/eval_director_fraud.py
-------------------------------
Targeted evaluation: does a director's fraud history make us score the COMPANY
high -- and does that fact survive all the way to the final brief?

Two modes
---------
1. OFFLINE (default -- runs anywhere, no network, no LLM):
       python scripts/eval_director_fraud.py

   Drives the REAL FraudRetriever logic + REAL deterministic projection with a
   disqualified, phoenix-pattern director (shaped like the public Companies
   House disqualified-officers register) and shows the resulting brief fields.
   Proves the *propagation mechanism* deterministically.

2. LIVE (run on a machine where COMPANIES_HOUSE_API_KEY, OPEN_SANCTIONS_API_KEY
   and the Groq/LLM key are set and reachable -- i.e. NOT this sandbox):
       python scripts/eval_director_fraud.py --live --company 01234567 \
              --name "EXAMPLE LIMITED" --expect-director "JANE DOE"

   Runs the full compiled graph against a real company and asserts the overall
   risk level is HIGH or CRITICAL and (optionally) that the named director
   appears in the brief's disqualified_officers. Pick a company from the live
   Insolvency Service disqualified-directors register so the assertion is real.

Exit code 0 = pass, 1 = fail.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.retrievers.fraud_retriever import FraudRetriever, _PHOENIX_CRITICAL
from src.evidence_facts import extract_underwriting_evidence_facts
from src.schemas import ResearchDimension, RiskLevel, UnderwritingAssessment


# -- Offline fixture: a disqualified phoenix director --------------------------
_DIR = "JOHN MICHAEL CARVER"
_CO_NO = "09988776"
_CO = "NORTHGATE TRADING LTD"


class _FakeFraudConnector:
    use_mock = False

    def fetch_officers_with_ids(self, company_number):
        return [
            {"name": _DIR, "officer_role": "director", "appointed_on": "2023-02-01",
             "resigned_on": None, "officer_id": "disqOfficerID"},
            {"name": "SARAH ELLEN WHITMORE", "officer_role": "director",
             "appointed_on": "2023-02-01", "resigned_on": None,
             "officer_id": "cleanOfficerID"},
        ]

    def check_disqualification_by_id(self, officer_id):
        if officer_id == "disqOfficerID":
            return {"disqualifications": [{
                "disqualified_from": "2022-09-15",
                "disqualified_until": "2034-09-15",
                "reason": {"act": "company-directors-disqualification-act-1986",
                           "article": "6", "description_identifier": "fraudulent-trading"},
                "company_names": ["PHOENIX A LTD", "PHOENIX B LTD"]}]}
        return {}

    def search_disqualified_by_name(self, name):
        return []

    def fetch_appointment_history(self, officer_id):
        if officer_id != "disqOfficerID":
            return []
        return [
            {"company_name": "PHOENIX A LTD", "company_number": "07111111",
             "company_status": "dissolved", "appointed_on": "2016-01-01"},
            {"company_name": "PHOENIX B LTD", "company_number": "08222222",
             "company_status": "liquidation", "appointed_on": "2019-03-01"},
            {"company_name": "PHOENIX C LTD", "company_number": "09333333",
             "company_status": "dissolved", "appointed_on": "2021-05-01"},
            {"company_name": "PHOENIX D LTD", "company_number": "10444444",
             "company_status": "administration", "appointed_on": "2023-07-01"},
            {"company_name": _CO, "company_number": _CO_NO,
             "company_status": "active", "appointed_on": "2023-02-01"},
        ]


class _FakeSanctions:
    def screen_entities(self, company_name, officer_names):
        return {"hits": {}, "screened_count": 1 + len(officer_names), "total_hits": 0}


def run_offline() -> int:
    print("=" * 70)
    print("OFFLINE PROOF -- disqualified phoenix director -> company assessment")
    print("=" * 70)
    r = FraudRetriever()
    r.connector = _FakeFraudConnector()
    r._sanctions = _FakeSanctions()
    item = r.retrieve(_CO_NO, _CO)

    facts = extract_underwriting_evidence_facts(
        {ResearchDimension.FRAUD_SIGNALS.value: item}
    )
    brief = UnderwritingAssessment(
        company_name=_CO,
        company_number=_CO_NO,
        disqualified_officers=facts.disqualified_officers,
        phoenix_risk_score=facts.phoenix_risk_score,
        phoenix_evidence=facts.phoenix_evidence,
        sanctions_hits=facts.sanctions_hits,
        enforcement_actions=facts.enforcement_actions,
        psc_risk_flags=facts.psc_risk_flags,
    )

    print(f"Company .................. {brief.company_name} ({brief.company_number})")
    print(f"Fraud evidence quality ... {item.quality.value} (confidence {item.confidence})")
    print(f"Disqualified officers .... {brief.disqualified_officers}")
    print(f"Phoenix risk score ....... {brief.phoenix_risk_score:.2f}"
          f"  ({'CRITICAL' if brief.phoenix_risk_score >= _PHOENIX_CRITICAL else 'sub-critical'})")
    for line in brief.phoenix_evidence:
        print(f"    {line}")

    checks = {
        "fraud evidence is HIGH quality (not weak)": item.quality.value == "high",
        "director reached brief.disqualified_officers": _DIR in brief.disqualified_officers,
        "phoenix score is CRITICAL (>0.7)": brief.phoenix_risk_score >= _PHOENIX_CRITICAL,
    }
    print("-" * 70)
    ok = True
    for label, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed
    print("=" * 70)
    print("RESULT:", "PASS -- the director's fraud drives the company assessment"
          if ok else "FAIL")
    print("NOTE: overall_risk_level is the LLM's call at synthesis; run --live to")
    print("      confirm the LLM escalates to HIGH/CRITICAL on this evidence.")
    return 0 if ok else 1


def run_live(company: str, name: str | None, expect_director: str | None) -> int:
    from src.config import settings
    from src.graph import compiled_graph
    from src.state import create_initial_state

    settings.use_mock_data = False
    target = name or company
    query = f"Insurance underwriting due diligence on {target}, company number {company}."
    print(f"LIVE run -> {query}")
    result = asyncio.run(compiled_graph.ainvoke(create_initial_state(query)))
    brief = result.get("due_diligence_brief")
    if brief is None:
        print("FAIL: no brief produced. errors:", result.get("errors"))
        return 1

    level = getattr(brief, "overall_risk_level", None)
    level_val = level.value if isinstance(level, RiskLevel) else str(level)
    disq = getattr(brief, "disqualified_officers", [])
    print(f"overall_risk_level ....... {level_val}")
    print(f"disqualified_officers .... {disq}")
    print(f"decline_indicators ....... {getattr(brief, 'decline_indicators', [])}")
    print(f"referral_triggers ........ {getattr(brief, 'referral_triggers', [])}")

    ok = level_val in ("high", "critical")
    if expect_director:
        ok = ok and any(expect_director.lower() in d.lower() for d in disq)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Director-fraud propagation eval")
    ap.add_argument("--live", action="store_true", help="run the full graph live")
    ap.add_argument("--company", help="real company number (live mode)")
    ap.add_argument("--name", help="company name (live mode, optional)")
    ap.add_argument("--expect-director", help="assert this name in disqualified_officers")
    args = ap.parse_args()

    if args.live:
        if not args.company:
            ap.error("--live requires --company")
        return run_live(args.company, args.name, args.expect_director)
    return run_offline()


if __name__ == "__main__":
    raise SystemExit(main())
