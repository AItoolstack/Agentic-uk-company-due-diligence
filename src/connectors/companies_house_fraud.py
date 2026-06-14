"""
connectors/companies_house_fraud.py
--
Connector for Companies House fraud-signal endpoints.

Covers:
  1. Director disqualification register
     GET /disqualified-officers/natural/{officer_id}     -- by officer ID
     GET /disqualified-officers/natural/search?q={name}  -- by name (fallback)
  2. Officer appointment history (phoenix pattern detection)
     GET /officers/{officer_id}/appointments

The officer_id is extracted from the links.officer.appointments path returned
in the main officers endpoint:
  "/officers/{officer_id}/appointments"

Phoenix pattern logic lives in the retriever, not here. This connector
only fetches and normalises raw data.

Mock fallback: data/mock_sources/fraud_signals.json
"""

from __future__ import annotations

import re
from typing import Any

from src.config import settings
from src.connectors.base import BaseConnector, ConnectorError

_CH_BASE = settings.companies_house_base_url
_AUTH = (settings.companies_house_api_key, "")

# Company statuses that indicate financial failure
FAILED_STATUSES = frozenset({
    "dissolved",
    "liquidation",
    "administration",
    "receivership",
    "voluntary-arrangement",
    "converted-closed",
    "insolvency-proceedings",
})


class CompaniesHouseFraudConnector(BaseConnector):

    # ------------------------------------------------------------------
    # Officers with IDs
    # ------------------------------------------------------------------

    def fetch_officers_with_ids(self, company_number: str) -> list[dict[str, Any]]:
        """Fetch company officers and extract officer IDs from appointment links.

        Returns a list of dicts with keys:
          name, officer_role, appointed_on, resigned_on, officer_id (str or None)
        """
        if self.use_mock:
            return self._mock_officers_with_ids()

        url = f"{_CH_BASE}/company/{company_number}/officers"
        raw = self._get(url, params={"items_per_page": 50}, auth=_AUTH)
        items = raw.get("items", [])

        result = []
        for officer in items:
            appointments_path = (
                officer.get("links", {})
                       .get("officer", {})
                       .get("appointments", "")
            )
            officer_id = self._extract_officer_id(appointments_path)
            result.append({
                "name": officer.get("name", ""),
                "officer_role": officer.get("officer_role", ""),
                "appointed_on": officer.get("appointed_on", ""),
                "resigned_on": officer.get("resigned_on"),
                "officer_id": officer_id,
            })
        return result

    # ------------------------------------------------------------------
    # Disqualification checks
    # ------------------------------------------------------------------

    def check_disqualification_by_id(self, officer_id: str) -> dict[str, Any]:
        """Check the disqualification register by officer ID.

        Returns disqualification record if found, empty dict if officer is clean.
        The CH API returns 404 for clean officers (not an error -- expected path).
        """
        if self.use_mock:
            return {}

        url = f"{_CH_BASE}/disqualified-officers/natural/{officer_id}"
        try:
            return self._get(url, auth=_AUTH)
        except ConnectorError as e:
            if "HTTP error 404" in str(e):
                return {}  # 404 = clean record, not an error
            raise

    def search_disqualified_by_name(self, name: str) -> list[dict[str, Any]]:
        """Search disqualified officers register by name.

        Used when officer_id is unavailable. Returns list of matches.
        """
        if self.use_mock:
            return []

        url = f"{_CH_BASE}/disqualified-officers/natural/search"
        try:
            raw = self._get(url, params={"q": name, "items_per_page": 5}, auth=_AUTH)
            return raw.get("items", [])
        except ConnectorError:
            return []

    # ------------------------------------------------------------------
    # Appointment history (phoenix pattern)
    # ------------------------------------------------------------------

    def fetch_appointment_history(self, officer_id: str) -> list[dict[str, Any]]:
        """Fetch full appointment history for an officer.

        Returns list of appointment records with company_number, company_name,
        company_status, appointed_on, resigned_on.
        """
        if self.use_mock:
            return []

        url = f"{_CH_BASE}/officers/{officer_id}/appointments"
        try:
            raw = self._get(url, params={"items_per_page": 50}, auth=_AUTH)
            items = raw.get("items", [])
            return [
                {
                    "company_name": item.get("appointed_to", {}).get("company_name", ""),
                    "company_number": item.get("appointed_to", {}).get("company_number", ""),
                    "company_status": item.get("appointed_to", {}).get("company_status", ""),
                    "appointed_on": item.get("appointed_on", ""),
                    "resigned_on": item.get("resigned_on"),
                    "officer_role": item.get("officer_role", ""),
                }
                for item in items
            ]
        except ConnectorError:
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_officer_id(appointments_path: str) -> str | None:
        """Extract officer ID from path like /officers/{id}/appointments."""
        match = re.search(r"/officers/([^/]+)/appointments", appointments_path)
        return match.group(1) if match else None

    @staticmethod
    def _mock_officers_with_ids() -> list[dict[str, Any]]:
        """Minimal mock officers with synthetic IDs for testing."""
        return [
            {
                "name": "BLYTH, TS",
                "officer_role": "director",
                "appointed_on": "2020-01-15",
                "resigned_on": None,
                "officer_id": "mock_officer_001",
            },
            {
                "name": "CAVANAGH, Gary",
                "officer_role": "director",
                "appointed_on": "2021-06-01",
                "resigned_on": None,
                "officer_id": "mock_officer_002",
            },
            {
                "name": "HENDRA, Sujata",
                "officer_role": "director",
                "appointed_on": "2022-03-10",
                "resigned_on": None,
                "officer_id": "mock_officer_003",
            },
        ]
