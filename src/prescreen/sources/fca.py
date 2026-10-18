"""
FCA Financial Services Register (read-only).

Only used when the trade is regulated (SIC 64-66) or the user says the firm
should be authorised. The register isn't keyed on the Companies House number,
so a name match is shown with its FRN for the underwriter to confirm.

Response shapes are parsed tolerantly: search results have been seen with both
"Reference Number" and "FRN" keys, and names can carry a "(Postcode: ...)" suffix.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from src.prescreen.sources.http import NOT_FOUND, SourceError, get_json

SOURCE = "FCA Register"
PUBLIC_FIRM_URL = "https://register.fca.org.uk/s/firm?id="


class FCARegister:
    def __init__(self, client: httpx.AsyncClient | None, api_key: str, email: str, base_url: str,
                 fixtures: dict[str, Any] | None = None) -> None:
        self.client = client
        self.api_key = api_key
        self.email = email
        self.base_url = base_url.rstrip("/")
        self.fixtures = fixtures

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self.fixtures is not None:
            value = self.fixtures.get(path if not params else f"{path}?q={params.get('q','')}".lower(), NOT_FOUND)
            if isinstance(value, SourceError):
                raise value
            return value
        if not (self.api_key and self.email):
            raise SourceError(SOURCE, "no_key", "FCA_API_KEY / FCA_API_EMAIL not set")
        headers = {
            "X-Auth-Email": self.email,
            "X-Auth-Key": self.api_key,
            "Accept": "application/json",
        }
        return await get_json(self.client, SOURCE, f"{self.base_url}{path}", params=params, headers=headers)

    async def search_firms(self, name: str) -> list[dict[str, str]]:
        raw = await self._get("/Search", {"q": name, "type": "firm"})
        if raw is NOT_FOUND or not isinstance(raw, dict):
            return []
        data = raw.get("Data") or []
        if isinstance(data, dict):
            data = [data]
        out = []
        for item in data:
            frn = str(item.get("Reference Number") or item.get("FRN") or "").strip()
            label = item.get("Name") or item.get("Organisation Name") or ""
            out.append({
                "frn": frn,
                "name": re.sub(r"\s*\(Postcode:.*\)\s*$", "", label).strip(),
                "status": (item.get("Status") or "").strip(),
                "kind": item.get("Type of business or Individual", ""),
            })
        return [o for o in out if o["frn"]]

    async def disciplinary_history(self, frn: str) -> list[str]:
        raw = await self._get(f"/Firm/{frn}/DisciplinaryHistory")
        if raw is NOT_FOUND or not isinstance(raw, dict):
            return []
        data = raw.get("Data") or []
        if isinstance(data, dict):
            data = [data]
        lines = []
        for item in data:
            parts = [
                str(item.get(k, "")).strip()
                for k in ("ActionType", "Action Type", "TypeofAction", "Type of Action",
                          "EnforcementType", "Enforcement Type", "ActionEffectiveFrom",
                          "Action Effective From", "Effective From", "Description")
                if item.get(k)
            ]
            lines.append(" | ".join(dict.fromkeys(parts)) or str(item)[:200])
        return lines
