"""
UK Sanctions List (FCDO), matched locally.

Since 28 Jan 2026 the UK Sanctions List is the only UK list (the OFSI
Consolidated List closed). It's a free CSV download, so we fetch it, keep a
copy for a day, and match the company, its directors and its PSCs locally.
That avoids per-query costs and the non-commercial terms of free APIs, and
it lets the report state exactly which list version was used.

The parser finds columns by header name rather than position, and refuses
(couldn't check) if the columns it needs aren't there, instead of guessing.
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from src.prescreen.identity import normalise_name
from src.prescreen.sources.http import SourceError

SOURCE = "UK Sanctions List"
LIST_URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv"
PUBLIC_URL = "https://www.gov.uk/government/publications/the-uk-sanctions-list"
_TITLES = {"MR", "MRS", "MS", "MISS", "DR", "SIR", "DAME", "LORD", "LADY", "BARONESS", "BARON", "PROF", "REV"}


def person_tokens(name: str) -> frozenset[str]:
    s = re.sub(r"[^A-Za-z ]+", " ", (name or "").upper())
    return frozenset(t for t in s.split() if len(t) > 1 and t not in _TITLES)


@dataclass
class ListEntry:
    unique_id: str
    kind: str  # individual | entity | ship
    names: list[str] = field(default_factory=list)
    dobs: list[str] = field(default_factory=list)  # as published, e.g. 12/05/1960 or dd/mm/1960
    regime: str = ""


@dataclass
class SanctionsList:
    entries: list[ListEntry]
    fetched_at: str
    source_note: str

    @property
    def size(self) -> int:
        return len(self.entries)


def _col(header: list[str], *needles: str) -> int | None:
    low = [h.strip().lower() for h in header]
    for needle in needles:
        for i, h in enumerate(low):
            if h == needle:
                return i
    for needle in needles:
        for i, h in enumerate(low):
            if needle in h:
                return i
    return None


def parse_csv(text: str, fetched_at: str = "", source_note: str = "") -> SanctionsList:
    rows = list(csv.reader(io.StringIO(text)))
    header_idx = next(
        (i for i, r in enumerate(rows[:10]) if any(c.strip().lower() == "name 6" for c in r)), None
    )
    if header_idx is None:
        raise SourceError(SOURCE, "bad_response", "no 'Name 6' column in the CSV header")
    header = rows[header_idx]
    c_id = _col(header, "unique id")
    c_n6 = _col(header, "name 6")
    c_names = [_col(header, f"name {i}") for i in range(1, 6)]
    c_kind = _col(header, "individual, entity, ship", "entity type", "group type")
    c_dob = _col(header, "d.o.b", "dob", "date of birth")
    c_regime = _col(header, "regime name", "regime")
    if c_id is None or c_n6 is None:
        raise SourceError(SOURCE, "bad_response", "missing 'Unique ID' or 'Name 6' column")

    by_id: dict[str, ListEntry] = {}
    for r in rows[header_idx + 1:]:
        if len(r) <= max(c_id, c_n6):
            continue
        uid = r[c_id].strip()
        if not uid:
            continue

        def cell(i: int | None) -> str:
            return r[i].strip() if i is not None and i < len(r) else ""

        given = " ".join(cell(i) for i in c_names if cell(i))
        full = f"{given} {cell(c_n6)}".strip()
        entry = by_id.setdefault(uid, ListEntry(unique_id=uid, kind=cell(c_kind).lower(), regime=cell(c_regime)))
        if full and full not in entry.names:
            entry.names.append(full)
        dob = cell(c_dob)
        if dob and dob not in entry.dobs:
            entry.dobs.append(dob)
    if not by_id:
        raise SourceError(SOURCE, "bad_response", "the CSV had no entries")
    kinds = {e.kind.split()[0] if e.kind else "" for e in by_id.values()}
    if not kinds & {"individual"} or not kinds <= {"individual", "entity", "ship", ""}:
        # A wrong type column would silently stop all person screening. Refuse instead.
        raise SourceError(SOURCE, "bad_response", f"unexpected entry types {sorted(kinds)[:5]}")
    return SanctionsList(entries=list(by_id.values()), fetched_at=fetched_at, source_note=source_note)


_MEMORY: dict[str, tuple[float, SanctionsList]] = {}


async def load_list(client: httpx.AsyncClient, cache_dir: Path, max_age_hours: float = 24) -> SanctionsList:
    """The parsed list, kept in memory and on disk for up to a day."""
    held = _MEMORY.get("list")
    if held and time.time() - held[0] < max_age_hours * 3600:
        return held[1]
    cache = cache_dir / "UK-Sanctions-List.csv"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_hours * 3600:
        stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(cache.stat().st_mtime))
        parsed = parse_csv(cache.read_text(encoding="utf-8-sig"), stamp, "cached copy")
        _MEMORY["list"] = (cache.stat().st_mtime, parsed)
        return parsed
    try:
        resp = await client.get(LIST_URL, timeout=60)
    except httpx.HTTPError as exc:
        raise SourceError(SOURCE, "unavailable", type(exc).__name__) from exc
    if resp.status_code != 200:
        raise SourceError(SOURCE, "unavailable", f"HTTP {resp.status_code}")
    text = resp.content.decode("utf-8-sig", errors="replace")
    parsed = parse_csv(text, time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()), "downloaded")
    _MEMORY["list"] = (time.time(), parsed)
    try:  # write-then-rename, so a reader never sees half a file
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass  # read-only disk: the in-memory copy is enough
    return parsed


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

@dataclass
class Screened:
    """One name we screened, with what we know about it."""
    name: str
    role: str                 # company | previous name | director | PSC (individual) | PSC (company)
    is_person: bool
    dob_year: int | None = None
    dob_month: int | None = None


@dataclass
class Hit:
    screened: Screened
    entry: ListEntry
    matched_name: str
    strength: str             # strong | possible | cleared
    reason: str


def _dob_parts(dob: str) -> tuple[int | None, int | None]:
    iso = re.match(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$", dob or "")
    if iso:
        return int(iso.group(1)), int(iso.group(2))
    m = re.match(r"^\s*(?:(\d{1,2}|dd)/)?(?:(\d{1,2}|mm)/)?(\d{4})\s*$", dob or "", re.I)
    if not m:
        return None, None
    month = m.group(2)
    return int(m.group(3)), (int(month) if month and month.isdigit() else None)


_LEGAL = {"LTD", "PLC", "LLP", "LP", "CO", "INC", "LLC", "SA", "AG", "GMBH", "BV", "NV", "OOO", "JSC", "PJSC", "AO", "PAO", "ZAO"}


def _entity_key(name: str) -> str:
    """Company name without legal-form words, so 'X SHIPPING CO LTD' meets 'X Shipping Company'."""
    return " ".join(t for t in normalise_name(name).split() if t not in _LEGAL)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def screen(names: list[Screened], sl: SanctionsList) -> list[Hit]:
    hits: list[Hit] = []
    for s in names:
        if s.is_person:
            q = person_tokens(s.name)
            if len(q) < 2:
                continue
            q_sorted = " ".join(sorted(q))
        else:
            q_norm = _entity_key(s.name)
            if len(q_norm) < 3:
                continue
        for e in sl.entries:
            person_entry = e.kind.startswith("indiv")
            if s.is_person != person_entry:
                continue
            for listed in e.names:
                if s.is_person:
                    t = person_tokens(listed)
                    exact = t == q
                    close = not exact and len(t) >= 2 and (
                        _similar(" ".join(sorted(t)), q_sorted) >= 0.92 or t <= q or q <= t
                    )
                else:
                    ln = _entity_key(listed)
                    exact = ln == q_norm
                    close = not exact and _similar(ln, q_norm) >= 0.93
                if not (exact or close):
                    continue
                hits.append(_grade(s, e, listed, exact))
                break
    return hits


def _grade(s: Screened, e: ListEntry, listed: str, exact: bool) -> Hit:
    if not s.is_person:
        return Hit(s, e, listed, "possible",
                   "Exact name match on a listed entity" if exact else "Close name match on a listed entity")
    name_kind = "Exact name match" if exact else "Partial name match"
    if s.dob_year is None or not e.dobs:
        return Hit(s, e, listed, "possible", f"{name_kind}; no date of birth to compare")
    parsed_any = False
    for dob in e.dobs:
        year, month = _dob_parts(dob)
        if year is None:
            continue
        parsed_any = True
        if year == s.dob_year and (month is None or s.dob_month is None or month == s.dob_month):
            return Hit(s, e, listed, "strong" if exact else "possible",
                       f"{name_kind} and date of birth match ({dob})")
    if not parsed_any:
        return Hit(s, e, listed, "possible",
                   f"{name_kind}; the list's date of birth ({', '.join(e.dobs)}) couldn't be compared")
    return Hit(s, e, listed, "cleared",
               f"{name_kind} but date of birth differs (list: {', '.join(e.dobs)}; "
               f"Companies House: {s.dob_month or '?'}/{s.dob_year})")
