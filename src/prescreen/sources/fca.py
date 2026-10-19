"""
FCA Financial Services Register (read-only).

Only used when the trade is regulated (SIC 64-66) or the user says the firm
should be authorised. The register isn't keyed on the Companies House number,
so a name match is shown with its FRN for the underwriter to confirm.

Search result items carry 'URL', 'Status', 'Reference Number', 'Type of business or
Individual' and 'Name' (confirmed against the Register API client docs, 18 Oct 2026).
The old app read 'FRN', which isn't there, so Monzo came back "FCA status unknown".
Disciplinary history items: 'TypeofAction', 'EnforcementType', 'ActionEffectiveFrom',
'TypeofDescription'. Names can carry a "(Postcode: ...)" suffix.
"""

from __future__ import annotations

import re
from typing import Any, Callable

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
        self.last_result_info: dict[str, Any] = {}

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

    MAX_PAGES = 5

    async def search_firms(self, name: str, want: Callable[[dict[str, str]], bool] | None = None) -> list[dict[str, str]]:
        """The Register's search is fuzzy and paged ("REVOLUT LTD" puts Revolut Ltd past page 1, live).
        Follow ResultInfo's next link up to MAX_PAGES, stopping early once a row satisfies `want`."""
        raw = await self._get("/Search", {"q": name, "type": "firm"})
        if raw is NOT_FOUND or not isinstance(raw, dict):
            return []
        data = self._rows(raw)
        self.last_result_info = raw.get("ResultInfo") or {}
        pages = 1
        while pages < self.MAX_PAGES and self.fixtures is None:
            nxt = self._next_url(raw.get("ResultInfo"))
            if not nxt or (want and any(want(o) for o in self._parse(data))):
                break
            raw = await get_json(self.client, SOURCE, nxt, headers={
                "X-Auth-Email": self.email, "X-Auth-Key": self.api_key, "Accept": "application/json"})
            if raw is NOT_FOUND or not isinstance(raw, dict):
                break
            data += self._rows(raw)
            pages += 1
        self.last_result_info = {**self.last_result_info, "pages_read": pages}
        return self._parse(data)

    @staticmethod
    def _rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
        data = raw.get("Data") or []
        return [data] if isinstance(data, dict) else list(data)

    @staticmethod
    def _next_url(info: Any) -> str:
        if not isinstance(info, dict):
            return ""
        nxt = info.get("Next") or info.get("next") or ""
        # Only ever follow a link back to the Register itself.
        return nxt if isinstance(nxt, str) and nxt.startswith("https://register.fca.org.uk/") else ""

    @staticmethod
    def _parse(data: list[dict[str, Any]]) -> list[dict[str, str]]:
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
                for k in ("TypeofAction", "ActionType", "Type of Action", "EnforcementType",
                          "Enforcement Type", "ActionEffectiveFrom", "Action Effective From",
                          "TypeofDescription", "Description")
                if item.get(k)
            ]
            lines.append(" | ".join(dict.fromkeys(parts)) or str(item)[:200])
        return lines
