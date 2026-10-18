"""
Identity gate: pin the case to one Companies House number before anything else runs.

The old app let a model pick among search results and carried on with the name
only when nothing matched. On live data that produced a confident report about
the wrong company (Wilko) and "clean" checks that never ran (Thames Water, Apex).

Rules here, in order:
  1. A company number resolves if Companies House has it. Nothing else does.
  2. A name resolves only if exactly one search result matches it exactly
     (after normalising LTD/LIMITED, punctuation and "THE"), and that company is active.
  3. Anything else returns candidates, and the underwriter picks.
"""

from __future__ import annotations

import re

from src.prescreen.models import Candidate, IdentityResult
from src.prescreen.sources.companies_house import CompaniesHouse
from src.prescreen.sources.http import NOT_FOUND, SourceError

_NUMBER = re.compile(r"^(?:[A-Z]{2}\d{6}|\d{8}|\d{6,7})$")
_SUFFIXES = [
    (r"\bPUBLIC LIMITED COMPANY\b", "PLC"),
    (r"\bLIMITED LIABILITY PARTNERSHIP\b", "LLP"),
    (r"\bLIMITED\b", "LTD"),
    (r"\bCOMPANY\b", "CO"),
    (r"&", " AND "),
]


def normalise_number(raw: str) -> str | None:
    s = re.sub(r"\s+", "", raw or "").upper()
    if not _NUMBER.match(s):
        return None
    return s.zfill(8) if s.isdigit() else s


def normalise_name(name: str) -> str:
    s = (name or "").upper()
    for pattern, repl in _SUFFIXES:
        s = re.sub(pattern, repl, s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    s = re.sub(r"^THE\s+", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def _address(item: dict) -> str:
    if item.get("address_snippet"):
        return item["address_snippet"]
    addr = item.get("registered_office_address") or item.get("address") or {}
    parts = [addr.get(k, "") for k in ("address_line_1", "locality", "postal_code")]
    return ", ".join(p for p in parts if p)


async def resolve(ch: CompaniesHouse, query: str) -> IdentityResult:
    query = (query or "").strip()
    number = normalise_number(query)

    if number:
        try:
            profile = await ch.profile(number)
        except SourceError as err:
            return IdentityResult(resolved=False, message=err.plain)
        if profile is NOT_FOUND:
            return IdentityResult(
                resolved=False,
                message=f"Companies House has no company with number {number}.",
            )
        return IdentityResult(
            resolved=True,
            company_number=number,
            company_name=profile.get("company_name", ""),
            method="number",
        )

    if len(query) < 3:
        return IdentityResult(resolved=False, message="Enter a company name or number.")

    try:
        result = await ch.search(query)
    except SourceError as err:
        return IdentityResult(resolved=False, message=err.plain)
    items = [] if result is NOT_FOUND else (result.get("items") or [])

    target = normalise_name(query)
    candidates = [
        Candidate(
            company_number=i.get("company_number", ""),
            name=i.get("title") or i.get("company_name", ""),
            status=i.get("company_status", ""),
            date_of_creation=i.get("date_of_creation", ""),
            address=_address(i),
            exact_name_match=normalise_name(i.get("title") or i.get("company_name", "")) == target,
        )
        for i in items
        if i.get("company_number")
    ]
    exact = [c for c in candidates if c.exact_name_match]
    exact_active = [c for c in exact if c.status == "active"]

    if len(exact) == 1 and len(exact_active) == 1:
        c = exact_active[0]
        return IdentityResult(
            resolved=True,
            company_number=c.company_number,
            company_name=c.name,
            method="unique_exact_name",
            candidates=candidates,
            message=(
                "Matched on the exact registered name. Check the number against the proposal "
                "before relying on the report."
            ),
        )

    if not candidates:
        msg = "No Companies House match for that name. Try the company number from the proposal."
    elif not exact:
        msg = "No company has exactly that registered name. Pick the right one, or use the company number."
    else:
        msg = (
            f"{len(exact)} companies have that exact name"
            + (" (some dissolved)" if len(exact) != len(exact_active) else "")
            + ". Pick the one on the proposal."
        )
    return IdentityResult(resolved=False, candidates=candidates, message=msg)
