"""
connectors/companies_house.py
------------------------------
Connector for the Companies House REST API.

Endpoints covered:
  - GET /company/{company_number}            -> company profile
  - GET /company/{company_number}/officers   -> officers list
  - GET /company/{company_number}/filing-history -> filing history

Real API: https://developer.company-information.service.gov.uk
Auth: HTTP Basic with api_key as username and empty password.

Mock fallback files:
  - data/mock_sources/companies_house_profile.json
  - data/mock_sources/companies_house_officers.json
  - data/mock_sources/companies_house_filings.json
"""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.connectors.base import BaseConnector


class CompaniesHouseConnector(BaseConnector):

    def fetch_profile(self, company_number: str) -> dict[str, Any]:
        """Fetch company profile by Companies House number."""
        if self.use_mock:
            return self._load_mock("companies_house_profile.json")
        url = f"{settings.companies_house_base_url}/company/{company_number}"
        return self._get(url, auth=(settings.companies_house_api_key, ""))

    def fetch_officers(self, company_number: str) -> dict[str, Any]:
        """Fetch the list of officers for a company."""
        if self.use_mock:
            return self._load_mock("companies_house_officers.json")
        url = f"{settings.companies_house_base_url}/company/{company_number}/officers"
        return self._get(url, auth=(settings.companies_house_api_key, ""))

    def fetch_filing_history(
        self, company_number: str, items_per_page: int = 25
    ) -> dict[str, Any]:
        """Fetch recent filing history for a company."""
        if self.use_mock:
            return self._load_mock("companies_house_filings.json")
        url = f"{settings.companies_house_base_url}/company/{company_number}/filing-history"
        return self._get(
            url,
            params={"items_per_page": items_per_page},
            auth=(settings.companies_house_api_key, ""),
        )

    def search_company(self, query: str) -> dict[str, Any]:
        """Search for a company by name. Returns list of matches."""
        if self.use_mock:
            # Return a minimal search result pointing at Monzo
            return {"items": [{"company_number": "09446231", "title": "MONZO BANK LIMITED"}]}
        url = f"{settings.companies_house_base_url}/search/companies"
        return self._get(
            url,
            params={"q": query, "items_per_page": 5},
            auth=(settings.companies_house_api_key, ""),
        )
