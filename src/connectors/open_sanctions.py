"""
connectors/open_sanctions.py
--
Connector for the OpenSanctions matching API.

Screens company names and individual names against 150+ global sanctions
and watchlists simultaneously, including:
  - HM Treasury OFSI (UK sanctions)
  - OFAC (US Treasury)
  - EU Consolidated List
  - UN Security Council
  - Interpol Red Notices
  - PEP (Politically Exposed Persons) databases

API docs: https://www.opensanctions.org/docs/api/
Endpoint: POST https://api.opensanctions.org/match/default

Auth: Authorization: ApiKey <key>. Obtain a trial or licensed API key from
OpenSanctions.

Mock fallback: returns empty hits for all queried names.
"""

from __future__ import annotations

from typing import Any

from src.config import settings
from src.connectors.base import BaseConnector, ConnectorError

_MATCH_URL = f"{settings.open_sanctions_base_url}/match/default"


class OpenSanctionsConnector(BaseConnector):

    def screen_entities(
        self,
        company_name: str,
        officer_names: list[str],
        country: str = "GB",
    ) -> dict[str, Any]:
        """Screen company + officers against all OpenSanctions lists.

        Sends a single batched POST request with one query per entity.
        Returns a dict keyed by entity label with a list of hits.

        Args:
            company_name:   Registered company name to screen.
            officer_names:  List of director/officer names to screen.
            country:        ISO-2 country code to bias matching (default GB).

        Returns:
            {
              "hits": {
                "company": [...],      # list of matching sanctions records
                "officer_0": [...],
                "officer_1": [...],
                ...
              },
              "screened_count": int,
              "total_hits": int,
            }
        """
        if self.use_mock:
            return self._mock_screen_result(company_name, officer_names)

        queries: dict[str, Any] = {
            "company": {
                "schema": "Organization",
                "properties": {
                    "name": [company_name],
                    "country": [country],
                },
            }
        }
        for i, name in enumerate(officer_names[:10]):  # cap at 10 to control rate
            queries[f"officer_{i}"] = {
                "schema": "Person",
                "properties": {
                    "name": [name],
                    "country": [country],
                },
            }

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.open_sanctions_api_key:
            headers["Authorization"] = (
                f"ApiKey {settings.open_sanctions_api_key}"
            )

        try:
            raw = self._post(_MATCH_URL, payload={"queries": queries}, headers=headers)
        except ConnectorError as e:
            # OpenSanctions outage should not crash the pipeline
            return {
                "hits": {},
                "screened_count": 0,
                "total_hits": 0,
                "error": str(e),
            }

        responses = raw.get("responses", {})
        hits: dict[str, list[dict[str, Any]]] = {}
        total_hits = 0

        for key, resp in responses.items():
            results = resp.get("results", [])
            # Only include results above the default score threshold
            matched = [
                {
                    "caption": r.get("caption", ""),
                    "schema": r.get("schema", ""),
                    "score": r.get("score", 0.0),
                    "datasets": r.get("datasets", []),
                    "properties": {
                        "name": r.get("properties", {}).get("name", []),
                        "country": r.get("properties", {}).get("country", []),
                        "topics": r.get("properties", {}).get("topics", []),
                    },
                }
                for r in results
                if r.get("score", 0.0) >= 0.7  # score threshold for a meaningful hit
            ]
            if matched:
                hits[key] = matched
                total_hits += len(matched)

        return {
            "hits": hits,
            "screened_count": len(queries),
            "total_hits": total_hits,
        }

    @staticmethod
    def _mock_screen_result(
        company_name: str,
        officer_names: list[str],
    ) -> dict[str, Any]:
        """Return a clean mock result (no hits) for testing."""
        return {
            "hits": {},
            "screened_count": 1 + len(officer_names),
            "total_hits": 0,
            "note": "Mock sanctions screening -- no real API call made.",
        }
