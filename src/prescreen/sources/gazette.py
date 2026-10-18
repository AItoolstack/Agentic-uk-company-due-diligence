"""
The Gazette (London, Edinburgh and Belfast editions), corporate insolvency notices.

Free, no key. The search JSON was checked live on 18 Oct 2026:
  /insolvency/notice/data.json?text=<company number>
returns entries with `f:notice-code`, `title`, `published` and an HTML `content`
snippet. Searching by name alone is noisy (a creditor's name appears in the
petition it presents: "Thames Water Utilities" returns a 2001 petition Thames
Water brought against someone else), so we search by company number only.

Notice codes used (24xx = corporate insolvency):
  2450  petition to wind up            (live check: confirmed)
  2410  appointment of administrators  (third-party source)
  2443  appointment of liquidators     (seen in live results)
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from src.prescreen.sources.http import NOT_FOUND, SourceError, get_json

SOURCE = "The Gazette"
SEARCH = "https://www.thegazette.co.uk/insolvency/notice/data.json"
NOTICE_NAMES = {
    "2450": "Petition to wind up",
    "2452": "Winding-up order",
    "2410": "Administrators appointed",
    "2443": "Liquidators appointed",
    "2441": "Resolution for winding up",
    "2442": "Meeting of creditors",
    "2421": "Notice to creditors",
}


def _strip(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


class Gazette:
    def __init__(self, client: httpx.AsyncClient | None, fixtures: list[dict[str, Any]] | SourceError | None = None):
        self.client = client
        self.fixtures = fixtures

    async def insolvency_notices(self, company_number: str, company_name: str) -> list[dict[str, str]]:
        if self.fixtures is not None:
            if isinstance(self.fixtures, SourceError):
                raise self.fixtures
            return list(self.fixtures)
        raw = await get_json(self.client, SOURCE, SEARCH,
                             params={"text": company_number, "results-page-size": 50})
        if raw is NOT_FOUND or not isinstance(raw, dict):
            return []
        entries = raw.get("entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        out = []
        for e in entries:
            text = _strip(e.get("content") if isinstance(e.get("content"), str) else str(e.get("content", "")))
            title = e.get("title", "")
            # The search matched the company number in the full notice; the snippet is often
            # cut ("Company Number: …"), so the number can't be re-checked here. An 8-character
            # company number is specific enough to accept the match.
            links = e.get("link") or []
            url = next((l.get("@href") for l in links if isinstance(l, dict) and "/notice/" in l.get("@href", "")
                        and "data." not in l.get("@href", "") and l.get("@rel") != "self"), "")
            out.append({
                "code": str(e.get("f:notice-code", "")),
                "title": title,
                "published": (e.get("published") or "")[:10],
                "url": url or e.get("id", ""),
                "snippet": text[:240],
            })
        return out
