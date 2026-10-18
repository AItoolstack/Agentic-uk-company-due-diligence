"""
Shared HTTP helper for pre-screen sources.

The one rule here: a source never returns "empty" when it actually failed.
It either returns data, returns NOT_FOUND (a real answer: the record doesn't
exist), or raises SourceError, which the check turns into "couldn't check".
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class SourceError(Exception):
    """The source could not answer. `kind` is shown to the underwriter."""

    def __init__(self, source: str, kind: str, message: str) -> None:
        super().__init__(f"{source}: {kind}: {message}")
        self.source = source
        self.kind = kind  # no_key | rate_limited | unavailable | bad_response
        self.message = message

    @property
    def plain(self) -> str:
        return {
            "no_key": f"{self.source} isn't configured (no API key).",
            "rate_limited": f"{self.source} rate limit reached; try again in a minute.",
            "unavailable": f"{self.source} didn't respond ({self.message}).",
            "bad_response": f"{self.source} returned something unexpected ({self.message}).",
        }.get(self.kind, f"{self.source}: {self.message}")


class NotFound:
    """Sentinel: the source answered and the record doesn't exist."""

    def __repr__(self) -> str:  # pragma: no cover
        return "NOT_FOUND"


NOT_FOUND = NotFound()


async def get_json(
    client: httpx.AsyncClient,
    source: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    retries: int = 2,
) -> Any:
    """GET a JSON resource. 404 -> NOT_FOUND. 429 -> wait and retry, then SourceError."""
    attempt = 0
    while True:
        try:
            resp = await client.get(url, params=params, headers=headers, auth=auth)
        except httpx.HTTPError as exc:
            if attempt < retries:
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            raise SourceError(source, "unavailable", type(exc).__name__) from exc

        if resp.status_code == 404:
            return NOT_FOUND
        if resp.status_code == 429:
            if attempt < retries:
                attempt += 1
                wait = _retry_after(resp, default=2.0 * attempt)
                await asyncio.sleep(min(wait, 15.0))
                continue
            raise SourceError(source, "rate_limited", "HTTP 429")
        if resp.status_code in (401, 403):
            raise SourceError(source, "no_key", f"HTTP {resp.status_code}")
        if resp.status_code >= 500:
            if attempt < retries:
                attempt += 1
                await asyncio.sleep(0.5 * attempt)
                continue
            raise SourceError(source, "unavailable", f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise SourceError(source, "bad_response", f"HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(source, "bad_response", "not JSON") from exc


def _retry_after(resp: httpx.Response, default: float) -> float:
    try:
        return float(resp.headers.get("retry-after", default))
    except ValueError:
        return default
