"""
prescreen/caseview.py
What the case page (/case) needs on top of the report, built in code so the page,
the file note and the API agree. Nothing here changes the route.

  - news_view:      news grouped into events (articles backing one finding are one event) and sections
  - broker_email:   the broker email built from the broker questions (always available, never on a hold)
  - parse_money / parse_years: the proposal fields' parsing, the same rules as the page's "Read as"
  - case_row:       one row of the recent cases table (route, coverage, decision, cover, stale)
  - expected_decision / check_decision: what agreeing with the route means, and the decision rules
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from src.prescreen.casefile import COMPLIANCE_ONLY, coverage as _coverage
from src.prescreen.models import COVER_LINES

# ---------------------------------------------------------------------------
# News: events and sections
# ---------------------------------------------------------------------------

EVENT_WINDOW_DAYS = 14
# Which kinds of news can back a finding in each underwriting group.
_BACKING = {
    "regulatory": {"regulatory_action", "fraud_or_financial_crime", "sanctions"},
    "financial": {"insolvency_or_distress"},
}
SECTIONS = [
    ("about", "Events about this company"),
    ("maybe", "May be this company (brand only)"),
    ("group", "Other companies in the group"),
    ("set_aside", "Set aside (different business)"),
    ("background", "Background"),
    ("not_reviewed", "Not reviewed"),
]


def _iso(d: Any) -> date | None:
    try:
        return date.fromisoformat(str(d)[:10])
    except (TypeError, ValueError):
        return None


def _adverse(it: dict[str, Any]) -> bool:
    return it.get("category") not in ("", "neutral", None) and it.get("severity") in ("medium", "high")


def _names(it: dict[str, Any], company_name: str) -> str:
    """'exact' when the article names this legal entity, else 'brand'."""
    from src.prescreen.media import names_this_entity
    return "exact" if it.get("entity_named") and names_this_entity(it["entity_named"], company_name, []) else "brand"


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def news_view(report: dict[str, Any]) -> dict[str, Any]:
    """Events: a finding plus the articles that back it (same kind of event, published within 14 days of the
    event date, or cited by the finding itself), then the remaining adverse articles about this company, one
    event per category. Only articles read as about this company (or brand-only, inside a finding's event) go
    into events; a group company's news is never there."""
    items = [dict(it, i=i) for i, it in enumerate(report.get("media") or [])]
    # An article the model never classified (no model, or the review failed) has no category: it wasn't read,
    # so it can't be "background" (Monzo, demo: two unread results showed as background).
    for it in items:
        if not it.get("category") and it.get("about_this_company") not in ("no",):
            it["about_this_company"] = "not_reviewed"
    name = report.get("company_name", "")
    reasons = report.get("route_reasons") or []
    used: set[int] = set()
    events: list[dict[str, Any]] = []

    def event_for(f: dict[str, Any], check: dict[str, Any], members: list[dict[str, Any]]) -> dict[str, Any]:
        members = sorted(members, key=lambda it: it.get("published") or "", reverse=True)
        idx = reasons.index(f["text"]) + 1 if f.get("text") in reasons else 0
        return {"id": f"e{len(events) + 1}", "title": members[0].get("title") if members else f.get("text"),
                "event_date": f.get("event_date") or (members[-1].get("published", "")[:10] if members else ""),
                "category": next((it.get("category") for it in members if it.get("category")), ""),
                "backs": {"check_id": check.get("check_id"), "rule_id": f.get("rule_id"), "route": f.get("route"),
                          "reason_index": idx, "text": f.get("text")},
                "articles": [dict(it, names=_names(it, name)) for it in members],
                "article_count": len(members)}

    # 1. Findings with an event date (FCA action, insolvency) and the articles about the same event.
    for c in report.get("checks") or []:
        kinds = _BACKING.get(c.get("group", ""))
        if not kinds:
            continue
        for f in c.get("findings") or []:
            when = _iso(f.get("event_date"))
            if not when or f.get("rule_id") in COMPLIANCE_ONLY or f.get("route") == "note" and \
                    f.get("rule_id") != "fca_discipline":
                continue
            members = [it for it in items if it["i"] not in used
                       and it.get("about_this_company") in ("yes", "unsure") and it.get("category") in kinds
                       and (d := _iso(it.get("published"))) and abs((d - when).days) <= EVENT_WINDOW_DAYS]
            if members:
                used.update(it["i"] for it in members)
                events.append(event_for(f, c, members))

    # 2. Media findings (their own titles), merged into an event above when they share articles.
    for c in report.get("checks") or []:
        if c.get("check_id") != "media":
            continue
        for f in c.get("findings") or []:
            if f.get("rule_id") not in ("media_high", "media_medium", "media_old"):
                continue
            titles = set((f.get("detail") or {}).get("titles") or [])
            urls = {s.get("url") for s in f.get("sources") or []}
            members = [it for it in items if it.get("about_this_company") == "yes"
                       and (it.get("title") in titles or it.get("url") in urls)]
            fresh = [it for it in members if it["i"] not in used]
            if fresh:
                used.update(it["i"] for it in fresh)
                events.append(event_for(f, c, fresh))

    # 3. Anything else adverse about this company: one event per category.
    rest = [it for it in items if it["i"] not in used and it.get("about_this_company") == "yes" and _adverse(it)]
    for cat in sorted({it.get("category") for it in rest}):
        members = [it for it in rest if it.get("category") == cat]
        used.update(it["i"] for it in members)
        members.sort(key=lambda it: it.get("published") or "", reverse=True)
        events.append({"id": f"e{len(events) + 1}", "title": members[0].get("title"),
                       "event_date": members[-1].get("published", "")[:10], "category": cat, "backs": None,
                       "articles": [dict(it, names=_names(it, name)) for it in members],
                       "article_count": len(members)})

    left = [it for it in items if it["i"] not in used]
    sections = {
        "about": events,
        "maybe": [it for it in left if it.get("about_this_company") == "unsure" and _adverse(it)],
        "group": [it for it in left if it.get("about_this_company") == "related"],
        "set_aside": [it for it in left if it.get("about_this_company") == "no"],
        "background": [it for it in left if it.get("about_this_company") in ("yes", "unsure") and not _adverse(it)],
        "not_reviewed": [it for it in left if it.get("about_this_company") == "not_reviewed"],
    }
    material = [e for e in events if e.get("backs") and e["backs"].get("route") != "note"] or events
    counts = {k: len(v) for k, v in sections.items()}
    bits = [f"{len(items)} read", _plural(len(events), "event")]
    if counts["maybe"]:
        bits.append(f"{counts['maybe']} worth a look")
    if counts["group"]:
        bits.append(_plural(counts["group"], "other group company", "other group companies"))
    if counts["set_aside"]:
        bits.append(f"{counts['set_aside']} set aside")
    if counts["background"]:
        bits.append(f"{counts['background']} background")
    if counts["not_reviewed"]:
        bits.append(f"{counts['not_reviewed']} not reviewed")
    return {"read": len(items), "events": events, "sections": sections, "counts": counts,
            "material": material[:3], "summary": " · ".join(bits),
            "section_titles": dict(SECTIONS)}


# ---------------------------------------------------------------------------
# Broker email
# ---------------------------------------------------------------------------

def _title(name: str) -> str:
    """MONZO BANK LIMITED -> Monzo Bank Limited, keeping short all-caps tokens like PLC, LLP, UK."""
    keep = {"PLC", "LLP", "UK", "LP", "CIC", "UAB", "DAC", "AG", "SA", "NV", "BV"}
    return " ".join(w if w in keep else (w[:1] + w[1:].lower() if w.isupper() else w) for w in (name or "").split())


def cover_words(cover_lines: list[str]) -> str:
    names = [COVER_LINES[c] for c in cover_lines or [] if c in COVER_LINES]
    if not names:
        return ""
    names = [names[0]] + [n[0].lower() + n[1:] if not n.startswith(("D&O", "Employers")) else n for n in names[1:]]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def is_hold(report: dict[str, Any]) -> bool:
    return report.get("route") == "compliance_hold" or any(
        f.get("rule_id") in COMPLIANCE_ONLY for c in report.get("checks") or [] for f in c.get("findings") or [])


def broker_email(report: dict[str, Any], underwriter: str = "") -> dict[str, Any]:
    """Built from the broker questions, so it is always available and says exactly what the questions say.
    On a compliance hold there is no email at all: nothing can be sent by mistake."""
    if is_hold(report):
        return {"available": False, "reason": "Held until compliance clears the case.", "subject": "", "body": "",
                "text": ""}
    qs = report.get("broker_questions") or []
    if not qs:
        return {"available": False, "reason": "No questions for the broker.", "subject": "", "body": "", "text": ""}
    name = _title(report.get("company_name", ""))
    subject = f"{name} ({report.get('company_number', '')}): information needed"
    cover = cover_words((report.get("request") or {}).get("cover_lines") or [])
    lead = (f"Before we can quote {cover} for {name}, please could you help with the following:" if cover
            else f"Before we can go further with {name}, please could you help with the following:")
    lines = ["Dear [Broker name],", "", lead, ""]
    lines += [f"{i}. {q['question']}" for i, q in enumerate(qs, 1)]
    lines += ["", "Kind regards,", underwriter.strip() or "[Your name]"]
    body = "\n".join(lines)
    return {"available": True, "reason": "", "subject": subject, "body": body,
            "text": f"Subject: {subject}\n\n{body}", "question_count": len(qs)}


# ---------------------------------------------------------------------------
# Proposal fields: the same parsing as the page ("Read as £2,400,000")
# ---------------------------------------------------------------------------

_MONEY = re.compile(r"£? ?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))? ?(k|m|mn|bn|b)?", re.I)
_MULT = {"k": 10**3, "m": 10**6, "mn": 10**6, "b": 10**9, "bn": 10**9}


class ParseError(ValueError):
    pass


def parse_money(text: Any) -> int | None:
    """2400000, 2,400,000, £2.4m, 750k, 1.2bn -> whole pounds. Empty -> None. Anything else raises ParseError
    with an example; nothing is stripped silently ("two million", "2.4.1", "£2,40,000" are refused)."""
    if text is None:
        return None
    if isinstance(text, bool):
        raise ParseError("Enter an amount in pounds, for example £2.4m or 2,400,000.")
    if isinstance(text, (int, float)):
        if text < 0 or (isinstance(text, float) and not text.is_integer()):
            raise ParseError("Enter an amount in whole pounds, for example 2400000.")
        return int(text)
    s = str(text).strip().replace("£", "£")
    if not s:
        return None
    m = _MONEY.fullmatch(s)
    if not m:
        raise ParseError("Enter an amount in pounds, for example £2.4m, 750k or 2,400,000.")
    whole, frac, unit = m.group(1), m.group(2) or "", (m.group(3) or "").lower()
    if "," in whole and not re.fullmatch(r"\d{1,3}(,\d{3})+", whole):
        raise ParseError("Commas go between thousands, for example 2,400,000.")
    mult = _MULT.get(unit, 1)
    if frac and mult == 1:
        if set(frac) != {"0"}:
            raise ParseError("Enter whole pounds, for example 2400000, or use m or k: £2.4m.")
        frac = ""
    value = int(whole.replace(",", "")) * mult + (int(frac) * mult // 10 ** len(frac) if frac else 0)
    if frac and (int(frac) * mult) % (10 ** len(frac)):
        raise ParseError("That's not a whole number of pounds; enter it in full, for example 2,400,000.")
    if value > 10**12:
        raise ParseError("That amount is too large to be a turnover; check it.")
    return value


def parse_years(text: Any) -> int | None:
    """Whole years only: "1.5" is an error, never read as 15."""
    if text is None:
        return None
    if isinstance(text, bool):
        raise ParseError("Enter whole years, for example 2.")
    if isinstance(text, int):
        if not 0 <= text <= 300:
            raise ParseError("Enter whole years between 0 and 300.")
        return text
    s = str(text).strip()
    if not s:
        return None
    if not re.fullmatch(r"\d{1,3}", s):
        raise ParseError("Whole years only, for example 2.")
    v = int(s)
    if v > 300:
        raise ParseError("Enter whole years between 0 and 300.")
    return v


def money_words(value: int | None) -> str:
    return "" if value is None else f"£{value:,}"


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

# What agreeing with each route means. On a hold the only decision is "referred to compliance".
EXPECTED = {"note": "proceed", "refer": "refer", "outside_appetite": "decline", "compliance_hold": "compliance"}
OVERRIDE_MIN_REASON = 20


def expected_decision(route: str) -> str:
    return EXPECTED.get(route, "refer")


def check_decision(route: str, decision: str, reason: str, identity_confirmed: bool | None) -> tuple[bool, str]:
    """Returns (overrides_route, error). Error is "" when the decision can be recorded.
    identity_confirmed None = an older client that doesn't send it (the page at /): not enforced there."""
    # Required since /case became the main page (20 Oct): no decision without the identity match.
    if not identity_confirmed:
        return False, "Confirm the company number and registered address match the proposal first."
    if route == "compliance_hold":
        # The previous page recorded a hold as "refer"; both mean referred to compliance.
        if decision not in ("compliance", "refer"):
            return False, "This case is held for compliance: only a referral to compliance can be recorded."
        return False, ""
    if decision == "compliance":
        return False, "Referred to compliance is only for a case on compliance hold."
    overrides = decision != expected_decision(route)
    if overrides and len((reason or "").strip()) < OVERRIDE_MIN_REASON:
        return True, (f"This overrides the recommended route, so give a reason of at least "
                      f"{OVERRIDE_MIN_REASON} characters.")
    return overrides, ""


# ---------------------------------------------------------------------------
# Recent cases
# ---------------------------------------------------------------------------

STALE_HOURS = 24
_SHORT_COVER = {"property": "Property", "liability": "EL / PL", "professional_indemnity": "PI",
                "management_liability": "D&O", "financial_institutions": "FI", "cyber": "Cyber",
                "motor_fleet": "Motor fleet"}


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def is_stale(created_at: Any, now: datetime | None = None) -> bool:
    d = _when(created_at)
    now = now or datetime.now(timezone.utc)
    return bool(d and (now - d).total_seconds() > STALE_HOURS * 3600)


def case_row(payload: dict[str, Any], decisions: list[dict[str, Any]] | None = None,
             now: datetime | None = None) -> dict[str, Any]:
    """One row of the recent cases table. Works from a full report payload."""
    decisions = decisions if decisions is not None else (payload.get("decisions") or [])
    last = decisions[-1] if decisions else None
    cov = payload.get("coverage") or (_coverage(payload.get("checks") or []) if payload.get("checks") else {})
    lines = (payload.get("request") or {}).get("cover_lines") or []
    return {
        "report_id": payload.get("report_id"), "company_number": payload.get("company_number"),
        "company_name": payload.get("company_name"), "route": payload.get("route"),
        "created_at": str(payload.get("created_at") or ""),
        "coverage": {"completed": cov.get("completed"), "applies": cov.get("applies"),
                     "couldnt_check": cov.get("couldnt_check", 0), "not_covered": cov.get("not_covered", 0)},
        "decision": ({"decision": last.get("decision"), "underwriter": last.get("underwriter", ""),
                      "recorded_at": str(last.get("recorded_at", "")),
                      "overrides_route": bool(last.get("overrides_route"))} if last else None),
        "decision_count": len(decisions),
        "cover": [_SHORT_COVER.get(c, c) for c in lines],
        "stale": is_stale(payload.get("created_at"), now),
        "data": (payload.get("meta") or {}).get("data", payload.get("data", "")),
        "rerun_of": payload.get("rerun_of", ""),
        "run_by": (payload.get("meta") or {}).get("run_by", "owner"),
    }


FILTERS = {
    "all": lambda r: True,
    "pending": lambda r: r.get("decision") is None,
    "refer": lambda r: r.get("route") == "refer",
    "outside_appetite": lambda r: r.get("route") == "outside_appetite",
    "hold": lambda r: r.get("route") == "compliance_hold",
}


def decorate(payload: dict[str, Any]) -> dict[str, Any]:
    """Adds the case-page fields to a stored report (older reports were saved without them). In place."""
    if "news_view" not in payload:
        payload["news_view"] = news_view(payload)
    if "broker_email" not in payload:
        payload["broker_email"] = broker_email(payload)
    payload["expected_decision"] = expected_decision(payload.get("route", ""))
    payload["stale"] = is_stale(payload.get("created_at"))
    payload.setdefault("sources_read", [])
    payload.setdefault("rerun_of", "")
    return payload
