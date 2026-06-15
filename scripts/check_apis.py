"""
scripts/check_apis.py
--
Quick connectivity check for real API keys.

Usage (from project root with venv active):
  python scripts/check_apis.py

Prints PASS/FAIL for each connector so you can confirm keys are
working before running the full pipeline.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

# Make sure src is importable from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check(label: str, fn: Callable[[], Any]) -> bool:
    try:
        result = fn()
        print(f"  PASS  {label}")
        if isinstance(result, dict):
            preview = str(result)[:120]
            print(f"        {preview}")
        return True
    except Exception as e:
        print(f"  FAIL  {label}: {e}")
        return False


def main() -> None:
    from src.config import settings
    from src.connectors.companies_house import CompaniesHouseConnector
    from src.connectors.fca_register import FCARegisterConnector
    from src.connectors.brave_search import BraveSearchConnector

    # Force real mode for this process regardless of .env.
    settings.use_mock_data = False

    print("=== API Connectivity Check ===")

    results = []

    # --- Companies House ---
    print("\nCompanies House:")
    ch = CompaniesHouseConnector()
    ch.use_mock = False

    results.append(check("search 'Monzo Bank'", lambda: ch.search_company("Monzo Bank")))
    results.append(check("fetch profile 09446231", lambda: ch.fetch_profile("09446231")))
    results.append(check("fetch officers 09446231", lambda: ch.fetch_officers("09446231")))
    results.append(check("fetch filings 09446231", lambda: ch.fetch_filing_history("09446231")))

    # --- FCA Register ---
    print("\nFCA Register:")
    fca = FCARegisterConnector()
    fca.use_mock = False

    results.append(check("search firm 'Monzo Bank'", lambda: fca.search_firm("Monzo Bank")))
    results.append(check("fetch firm FRN 730427", lambda: fca.fetch_firm("730427")))

    # --- Brave Search ---
    print("\nBrave Search:")
    brave = BraveSearchConnector()
    brave.use_mock = False

    if not settings.brave_search_api_key:
        print("  SKIP  BRAVE_SEARCH_API_KEY not set")
    else:
        results.append(check(
            "search 'Monzo Bank regulatory UK'",
            lambda: brave.search("Monzo Bank regulatory UK", count=3),
        ))

    # --- Summary ---
    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
