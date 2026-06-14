"""
connectors/fca_register.py
--
Connector for the FCA Financial Services Register API.

Endpoints covered:
  - GET /Firm/{firmReferenceNumber}  -> firm details
  - GET /Search?q={name}             -> firm name search

Real API: https://register.fca.org.uk/services/V0.1
Auth: The register.fca.org.uk API is public but requires browser-like
      headers to avoid 403 from Cloudflare WAF (python-requests UA is
      blocked). FCA_API_KEY adds an X-Auth-Key header if set.

The real API returns PascalCase keys nested under a "Data" object.
_normalize_firm() maps these to the flat snake_case schema the
fca_retriever expects (matching the mock fixture format).

Mock fallback file: data/mock_sources/fca_register.json
"""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.connectors.base import BaseConnector, ConnectorError

# Browser-like headers avoid 403 from Cloudflare WAF on register.fca.org.uk
_FCA_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://register.fca.org.uk/",
    "Origin": "https://register.fca.org.uk",
    "Connection": "keep-alive",
}


class FCARegisterConnector(BaseConnector):

    def fetch_firm(self, firm_reference_number: str) -> dict[str, Any]:
        """Fetch FCA register entry for a firm by reference number."""
        if self.use_mock:
            return self._load_mock("fca_register.json")
        if not firm_reference_number:
            raise ConnectorError("FCA firm reference number is required")
        url = f"{settings.fca_base_url}/Firm/{firm_reference_number}"
        headers = self._request_headers()
        raw = self._get(url, headers=headers)
        return self._normalize_firm(raw)

    def search_firm(self, query: str) -> dict[str, Any]:
        """Search the FCA register by firm name.

        Returns a dict with "Data" key containing a list of
        {"FRN": ..., "Organisation Name": ...} items, matching
        the shape entity_resolution_agent expects.
        """
        if self.use_mock:
            return {"Data": [{"FRN": "730427", "Organisation Name": "Monzo Bank Limited"}]}
        url = f"{settings.fca_base_url}/Search"
        headers = self._request_headers()
        return self._get(url, params={"q": query}, headers=headers)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request_headers(self) -> dict[str, str]:
        """Return browser-like headers, plus X-Auth-Key if configured.

        The FCA Register public API blocks python-requests User-Agent
        with 403. Sending browser-style headers avoids this.
        """
        headers = dict(_FCA_HEADERS)
        if settings.fca_api_key:
            headers["X-Auth-Key"] = settings.fca_api_key
        return headers

    def _normalize_firm(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize real FCA API response to flat snake_case schema.

        The real API wraps results in a "Data" key which may be either
        a dict (firm endpoint) or a list (search endpoint). We handle
        both shapes and map PascalCase keys to the schema the retriever
        and mock fixture use.
        """
        data = raw.get("Data", raw)

        # /Firm/{FRN} returns Data as a dict; /Search returns a list.
        if isinstance(data, list):
            firm: dict[str, Any] = data[0] if data else {}
        else:
            firm = data if isinstance(data, dict) else {}

        permissions_raw = firm.get("Permissions", firm.get("BusinessActivities", []))
        if isinstance(permissions_raw, list):
            permissions = [
                p.get("Permission", p) if isinstance(p, dict) else str(p)
                for p in permissions_raw
            ]
        else:
            permissions = []

        regulators_raw = firm.get("Regulators", [])
        regulators = [
            {"name": r.get("Name", r), "role": r.get("Role", "")}
            if isinstance(r, dict) else {"name": str(r), "role": ""}
            for r in (regulators_raw if isinstance(regulators_raw, list) else [])
        ]

        return {
            "firm_reference_number": str(firm.get("FRN", firm.get("Organisation FRN", ""))),
            "organisation_name": firm.get("Organisation Name", ""),
            "status": firm.get("Status", "unknown"),
            "status_effective_date": firm.get("Status Effective Date", ""),
            "firm_type": firm.get("Firm Type", ""),
            "permissions": permissions,
            "requirements": firm.get("Requirements", []),
            "appointed_representatives": firm.get("AppointedRepresentatives", []),
            "disciplinary_history": firm.get("DisciplinaryHistory", []),
            "regulators": regulators,
            "pra_regulated": "Prudential Regulation Authority" in str(regulators),
        }
