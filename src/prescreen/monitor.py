"""
Watching companies after the pre-screen.

A snapshot is the small set of facts worth comparing day to day. The daily
re-check runs the rule-based checks again (no news, so no model calls),
compares the new snapshot with the stored one, and records what changed.

What counts as a change, and how serious it is:
  - the recommended route got worse                          -> the new route
  - a new finding that refers / is outside appetite / hold   -> that finding's route
  - company status changed                                   -> note (the finding carries the weight)
  - directors or owners joined or left                       -> note (refer for owners)
  - new accounts filed                                       -> note
A route getting better is recorded as a note, so nothing silently disappears.
"""

from __future__ import annotations

from typing import Any

from src.prescreen.models import Route

RANK = {r.value: r.rank for r in Route}
CHECKS_IN_SNAPSHOT = ("status", "filings", "insolvency", "directors_disqualified", "directors_insolvency",
                      "ownership", "sanctions", "fca", "charges")


def snapshot(report: dict[str, Any]) -> dict[str, Any]:
    company = report.get("company") or {}
    findings = []
    for c in report.get("checks") or []:
        if c.get("check_id") not in CHECKS_IN_SNAPSHOT:
            continue
        for f in c.get("findings") or []:
            if f.get("route") != "note":
                findings.append({"rule_id": f.get("rule_id"), "route": f.get("route"), "text": f.get("text"),
                                 "check": c.get("title")})
    return {
        "route": report.get("route"),
        "report_id": report.get("report_id"),
        "status": company.get("status", ""),
        "status_detail": company.get("status_detail", ""),
        "last_accounts": (company.get("last_accounts") or {}).get("made_up_to", ""),
        "officers": company.get("officer_names") if company.get("officers_read") else None,
        "owners": company.get("owner_names") if company.get("owners_read") else None,
        "findings": findings,
        "check_status": {c.get("check_id"): c.get("status") for c in report.get("checks") or []
                         if c.get("check_id") in CHECKS_IN_SNAPSHOT},
        "request": report.get("request") or {},
    }


def _key(f: dict[str, Any]) -> str:
    return f"{f.get('rule_id')}|{f.get('text')}"


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    ro, rn = old.get("route") or "note", new.get("route") or "note"

    def add(kind: str, severity: str, summary: str, before: Any = None, after: Any = None) -> None:
        changes.append({"kind": kind, "severity": severity, "summary": summary, "before": before, "after": after,
                        "route_before": ro, "route_after": rn})

    if RANK.get(rn, 0) > RANK.get(ro, 0):
        add("route", rn, f"Recommended route changed from {_word(ro)} to {_word(rn)}.", ro, rn)
    elif RANK.get(rn, 0) < RANK.get(ro, 0):
        add("route", "note", f"Recommended route eased from {_word(ro)} to {_word(rn)}.", ro, rn)

    old_f = {_key(f): f for f in old.get("findings") or []}
    for k, f in {_key(f): f for f in new.get("findings") or []}.items():
        if k not in old_f:
            add(_kind(f.get("rule_id", "")), f.get("route", "refer"), f"New: {f.get('text')}", None, f)
    for k, f in old_f.items():
        if k not in {_key(x) for x in new.get("findings") or []}:
            add(_kind(f.get("rule_id", "")), "note", f"No longer found: {f.get('text')}", f, None)

    if (old.get("status"), old.get("status_detail")) != (new.get("status"), new.get("status_detail")):
        before = " ".join(x for x in (old.get("status"), old.get("status_detail")) if x).replace("-", " ")
        after = " ".join(x for x in (new.get("status"), new.get("status_detail")) if x).replace("-", " ")
        add("status", "note", f"Companies House status changed: {before or 'unknown'} → {after or 'unknown'}.",
            before, after)

    for field, label, severity in (("officers", "Directors", "note"), ("owners", "Owners (PSCs)", "refer")):
        a, b = old.get(field), new.get(field)
        if a is None or b is None:  # one of the reads failed: don't invent joiners/leavers
            continue
        joined, left = sorted(set(b) - set(a)), sorted(set(a) - set(b))
        if joined:
            add(field, severity, f"{label} added: {', '.join(joined[:5])}. Was this presented?", None, joined)
        if left:
            add(field, severity, f"{label} removed: {', '.join(left[:5])}.", left, None)

    if new.get("last_accounts") and new.get("last_accounts") != old.get("last_accounts"):
        add("filings", "note", f"New accounts filed, made up to {new['last_accounts']}.",
            old.get("last_accounts"), new.get("last_accounts"))

    newly_unchecked = [k for k, v in (new.get("check_status") or {}).items()
                       if v == "couldnt_check" and (old.get("check_status") or {}).get(k) != "couldnt_check"]
    if newly_unchecked:
        add("coverage", "note", "Couldn't be re-checked today: " + ", ".join(newly_unchecked).replace("_", " ") +
            ". Not a change in the company, a gap in today's check.")
    return changes


def _word(route: str) -> str:
    return {"note": "note only", "refer": "refer", "outside_appetite": "outside appetite",
            "compliance_hold": "compliance hold"}.get(route, route)


def _kind(rule_id: str) -> str:
    if rule_id.startswith("sanctions"):
        return "sanctions"
    if rule_id.startswith(("winding_up", "gazette", "insolvency", "company_insolvent", "company_closed")):
        return "insolvency"
    if rule_id.startswith(("strike_off", "status", "office")):
        return "status"
    if rule_id.startswith(("accounts", "confirmation", "dormant")):
        return "filings"
    if rule_id.startswith(("director", "disq", "phoenix", "no_directors")):
        return "officers"
    if rule_id.startswith("psc"):
        return "owners"
    if rule_id.startswith("fca"):
        return "fca"
    if rule_id.startswith("charges"):
        return "charges"
    return "other"
