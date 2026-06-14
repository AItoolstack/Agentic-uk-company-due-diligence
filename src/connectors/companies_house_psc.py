"""
connectors/companies_house_psc.py
--
Connector for the Companies House Persons with Significant Control (PSC) register.

Endpoints used:
  GET /company/{number}/persons-with-significant-control
      Returns list of PSCs (natural persons, corporates, legal entities).
  GET /company/{number}/persons-with-significant-control-statements
      Returns filed statements when no specific PSC is named (e.g. exemptions,
      "steps to find PSC not yet completed", RNS-exempt listed companies).

PSC kinds returned by the API:
  individual-person-with-significant-control
  corporate-entity-person-with-significant-control
  legal-person-with-significant-control
  super-secure-person-with-significant-control  -- name redacted, high-risk signal

Risk classification performed in the retriever, not here. This connector
only fetches, normalises, and categorises PSC records by kind.

Mock fallback: data/mock_sources/psc.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.config import settings
from src.connectors.base import BaseConnector, ConnectorError

_CH_BASE = settings.companies_house_base_url
_AUTH = (settings.companies_house_api_key, "")

# Jurisdictions associated with financial opacity / AML risk (lower-case for comparison)
HIGH_RISK_JURISDICTIONS: frozenset[str] = frozenset({
    "british virgin islands",
    "bvi",
    "cayman islands",
    "panama",
    "marshall islands",
    "seychelles",
    "belize",
    "vanuatu",
    "samoa",
    "nauru",
    "cook islands",
    "niue",
    "anguilla",
    "turks and caicos islands",
    "turks and caicos",
    "labuan",
    "liechtenstein",
    "monaco",
})

# UK Crown Dependencies -- lower risk but still flagged for completeness
CROWN_DEPENDENCY_JURISDICTIONS: frozenset[str] = frozenset({
    "jersey",
    "guernsey",
    "isle of man",
})

# Statement kinds that indicate opacity or unresolved PSC identification
OPACITY_STATEMENT_KINDS: frozenset[str] = frozenset({
    "no-individual-or-entity-with-signficant-control",
    "steps-to-find-psc-not-yet-completed",
    "psc-exists-but-not-identified",
    "psc-details-not-confirmed",
    "declaration-with-verification-statement",
})


class CompaniesHousePSCConnector(BaseConnector):
    """Fetches and categorises PSC register data for a company."""

    # ------------------------------------------------------------------
    # Primary fetch
    # ------------------------------------------------------------------

    def fetch_pscs(self, company_number: str) -> dict[str, Any]:
        """Fetch the full PSC register for a company.

        Returns the raw API response dict with an added top-level key
        "items_by_kind" that groups items into:
          natural_persons, corporates, legal_entities, super_secure
        """
        if self.use_mock:
            return self._load_mock_pscs()

        url = f"{_CH_BASE}/company/{company_number}/persons-with-significant-control"
        try:
            raw = self._get(url, params={"items_per_page": 50}, auth=_AUTH)
        except ConnectorError as e:
            if "HTTP error 404" in str(e):
                return {"active_count": 0, "ceased_count": 0, "items": [], "items_by_kind": _empty_by_kind()}
            raise

        raw["items_by_kind"] = _categorise_items(raw.get("items", []))
        return raw

    def fetch_psc_statements(self, company_number: str) -> list[dict[str, Any]]:
        """Fetch PSC control statements (filed when PSC is exempted or unidentified).

        Returns list of statement dicts; empty list if endpoint returns 404.
        """
        if self.use_mock:
            return []

        url = f"{_CH_BASE}/company/{company_number}/persons-with-significant-control-statements"
        try:
            raw = self._get(url, params={"items_per_page": 25}, auth=_AUTH)
            return raw.get("items", [])
        except ConnectorError as e:
            if "HTTP error 404" in str(e):
                return []
            raise

    # ------------------------------------------------------------------
    # Mock
    # ------------------------------------------------------------------

    @staticmethod
    def _load_mock_pscs() -> dict[str, Any]:
        mock_path = Path(__file__).parent.parent.parent / "data" / "mock_sources" / "psc.json"
        with mock_path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        raw["items_by_kind"] = _categorise_items(raw.get("items", []))
        return raw


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _categorise_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split a flat PSC list into typed buckets."""
    by_kind: dict[str, list[dict[str, Any]]] = _empty_by_kind()
    for item in items:
        kind = item.get("kind", "")
        if "individual" in kind:
            by_kind["natural_persons"].append(item)
        elif "corporate" in kind:
            by_kind["corporates"].append(item)
        elif "legal-person" in kind:
            by_kind["legal_entities"].append(item)
        elif "super-secure" in kind:
            by_kind["super_secure"].append(item)
    return by_kind


def _empty_by_kind() -> dict[str, list[dict[str, Any]]]:
    return {
        "natural_persons": [],
        "corporates": [],
        "legal_entities": [],
        "super_secure": [],
    }


def get_jurisdiction(psc_item: dict[str, Any]) -> str:
    """Extract the country/jurisdiction from a corporate or legal PSC.

    Checks identification.country_registered first, then address.country.
    Returns lower-case string for comparison against jurisdiction sets.
    """
    country = (
        psc_item.get("identification", {}).get("country_registered", "")
        or psc_item.get("address", {}).get("country", "")
    )
    return country.strip().lower()
