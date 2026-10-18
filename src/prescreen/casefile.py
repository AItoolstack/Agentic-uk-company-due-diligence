"""
prescreen/casefile.py
How a finished pre-screen is laid out for an underwriter. Nothing here changes
the route: the rules decide that. This module only sorts and words the result.

Four things:
  1. Groups. Each check belongs to one of five questions an underwriter asks.
     No scores per group; a group shows what was found and what wasn't looked at.
  2. Fraud tags. Some findings are also fraud indicators. That's a tag on the
     finding, not a sixth group, so the finding stays where an underwriter
     expects it (a phoenix pattern is still a management finding).
  3. Questions for the broker, one per finding that needs an answer. Sanctions
     findings never become broker questions: asking the broker could tip off
     the client. They go to compliance.
  4. Proposal vs public record, and a plain-text file note to paste into the
     underwriting system.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

GROUPS: list[dict[str, str]] = [
    {"id": "identity", "title": "Identity & ownership", "question": "Is this who the proposal says, and who owns it?"},
    {"id": "financial", "title": "Financial standing", "question": "Is it keeping up, and is there any sign of distress?"},
    {"id": "management", "title": "Management", "question": "Who runs it, and what is their record?"},
    {"id": "regulatory", "title": "Regulatory & legal", "question": "Is it allowed to do this, and is anyone acting against it?"},
    {"id": "reputation", "title": "Reputation", "question": "What is being said about it in public?"},
]
GROUP_TITLES = {g["id"]: g["title"] for g in GROUPS}

CHECK_GROUP = {
    "status": "identity",
    "age": "identity",
    "ownership": "identity",
    "trade": "identity",
    "filings": "financial",
    "insolvency": "financial",
    "charges": "financial",
    "ccj": "financial",
    "directors_disqualified": "management",
    "directors_insolvency": "management",
    "changes": "management",
    "sanctions": "regulatory",
    "fca": "regulatory",
    "media": "reputation",
}

# Findings that are also fraud indicators. Tag -> rule ids.
FRAUD_TAGS: dict[str, set[str]] = {
    "phoenix": {"phoenix_pattern"},
    "identity mismatch": {"trade_mismatch", "dormant_but_trading", "accounts_size_mismatch"},
    "address": {"office_disputed", "office_undeliverable"},
    "sanctions": {"sanctions_strong", "sanctions_possible"},
    "hidden ownership": {"psc_secrecy_jurisdiction", "psc_super_secure", "no_psc"},
}


def group_of(check_id: str) -> str:
    return CHECK_GROUP.get(check_id, "identity")


def fraud_tags(rule_id: str) -> list[str]:
    return [tag for tag, ids in FRAUD_TAGS.items() if rule_id in ids]


# ---------------------------------------------------------------------------
# Questions for the broker
# ---------------------------------------------------------------------------

# rule_id -> question. Plain, answerable, no accusation. {placeholders} come from the finding's detail.
BROKER_QUESTIONS: dict[str, str] = {
    "accounts_overdue_serious": "Accounts at Companies House are well overdue. Why, when will they be filed, and can the client send management accounts now?",
    "accounts_overdue": "Accounts at Companies House are overdue. When will they be filed, and can the client send management accounts in the meantime?",
    "accounts_size_mismatch": "The declared turnover is larger than the last filed accounts allow for. Which is right, and has the business grown since?",
    "charges_burst": "Several lenders have taken charges over the company in the last year. What has the business borrowed, and why?",
    "company_new": "The company is under a year old. Is this a new venture, or a continuation of an earlier business? If a continuation, which company?",
    "confirmation_overdue": "The confirmation statement is overdue at Companies House. Is the company still trading as presented?",
    "director_disqualified": "A director appears on the disqualified directors register. Please confirm the full board and dates of birth.",
    "director_previously_disqualified": "A director was disqualified in the past. Please confirm their role in the business now.",
    "director_insolvent_links": "A director has been involved with companies that went into insolvency. What happened, and what is different here?",
    "disq_possible": "A director's name matches the disqualified register but we can't confirm it's the same person. Please confirm their date of birth.",
    "dormant_but_trading": "The company files as dormant, but the proposal describes a trading business. Which company actually trades?",
    "fca_discipline": "The firm has a history of FCA disciplinary action. What changed afterwards?",
    "fca_not_found": "We couldn't find the firm on the FCA Register. Is it authorised, or an appointed representative of another firm (and which)?",
    "fca_not_authorised": "The FCA Register doesn't show the firm as currently authorised. What is its regulatory status today?",
    "gazette_insolvency_notice": "There is an insolvency notice in The Gazette for this company. What is the current position?",
    "insolvency_history": "Companies House shows an insolvency history. What happened, and is it closed?",
    "no_psc": "No one is registered as owning or controlling the company. Who are the owners?",
    "officers_joined": "New directors joined in the last year. Who are they, and what do they bring?",
    "officers_left": "Directors left in the last year. Why?",
    "phoenix_pattern": "A director's earlier company with a similar name went into insolvency. Is this business a continuation of it?",
    "psc_changed": "Ownership changed in the last year. Who bought in, and was anything else about the business changed?",
    "psc_secrecy_jurisdiction": "An owner is registered in a jurisdiction where ownership is hard to see. Who are the ultimate owners?",
    "psc_statement": "The company has told Companies House it can't identify or confirm its owners. Who are they?",
    "psc_super_secure": "An owner's details are protected on the register. Can the broker confirm who stands behind the business?",
    "strike_off_compulsory": "Companies House has started to strike the company off. Why, and what is being done to stop it?",
    "strike_off_voluntary": "The directors have applied to strike the company off. Why buy cover for a company that is closing?",
    "strike_off_unknown": "There is a proposal to strike the company off. Why, and what is being done about it?",
    "trade_mismatch": "The declared trade doesn't match the activity registered at Companies House. What does the business actually do?",
    "winding_up_petition": "A winding-up petition has been advertised. Has it been paid, dismissed or withdrawn?",
    "media_high": "There is serious adverse news about the company. What is the client's account of it?",
    "media_medium": "There is adverse news about the company. Has it been resolved?",
    "office_disputed": "The registered office address is disputed at Companies House. Where does the company actually operate?",
    "office_undeliverable": "Post to the registered office is being returned. Where does the company actually operate?",
    "status_unusual": "The company's status at Companies House is unusual. Is it trading as presented?",
}

# Never put to the broker: compliance handles these without tipping off the client.
COMPLIANCE_ONLY = {"sanctions_strong", "sanctions_possible"}


def broker_questions(checks: list[Any]) -> list[dict[str, Any]]:
    """One question per rule that needs an answer, worst route first, then in check order.
    `checks` are CheckResult models or their dicts."""
    seen: set[str] = set()
    out: list[tuple[int, int, dict[str, Any]]] = []
    for ci, c in enumerate(checks):
        c = _d(c)
        for f in c.get("findings") or []:
            rid = f.get("rule_id", "")
            if rid in COMPLIANCE_ONLY or rid in seen or rid not in BROKER_QUESTIONS:
                continue
            # Notes only get a question where a note is still worth asking about.
            if f.get("route") == "note" and rid not in {"company_new", "officers_joined", "officers_left",
                                                         "psc_changed", "director_previously_disqualified",
                                                         "fca_discipline", "media_medium"}:
                continue
            seen.add(rid)
            rank = {"note": 0, "refer": 1, "outside_appetite": 2, "compliance_hold": 3}.get(f.get("route"), 0)
            out.append((-rank, ci, {"rule_id": rid, "check_id": c.get("check_id"), "route": f.get("route"),
                                    "group": group_of(c.get("check_id", "")),
                                    "question": BROKER_QUESTIONS[rid], "because": f.get("text", "")}))
    return [q for _, _, q in sorted(out, key=lambda x: (x[0], x[1]))]


# ---------------------------------------------------------------------------
# Proposal vs public record
# ---------------------------------------------------------------------------

# Upper turnover limit implied by the last accounts filed (limits from 6 April 2025).
ACCOUNT_LIMITS = {
    "micro-entity": ("micro-entity", 1_000_000),
    "small": ("small", 15_000_000), "total-exemption-small": ("small", 15_000_000),
    "total-exemption-full": ("small", 15_000_000), "unaudited-abridged": ("small", 15_000_000),
    "audited-abridged": ("small", 15_000_000), "abridged": ("small", 15_000_000),
    "medium": ("medium", 54_000_000),
    "dormant": ("dormant", 0),
}


def _d(x: Any) -> dict[str, Any]:
    if isinstance(x, dict):
        return x
    return x.model_dump(mode="json") if hasattr(x, "model_dump") else dict(x)


def _tokens(name: str) -> frozenset[str]:
    s = re.sub(r"[^A-Za-z ]+", " ", (name or "").upper())
    return frozenset(t for t in s.split() if len(t) > 1 and t not in {"MR", "MRS", "MS", "DR", "SIR", "MISS"})


def _same_person(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    return a == b or (len(a & b) >= 2 and (a <= b or b <= a))


def _years(since: str, today: date) -> float | None:
    try:
        d = datetime.strptime(since[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return (today - d).days / 365.25


def proposal_vs_record(request: Any, company: dict[str, Any], checks: list[Any], today: date) -> list[dict[str, Any]]:
    """Rows of {item, proposal, record, verdict, note}. verdict: match | differs | not_given | unknown."""
    req = _d(request)
    rows: list[dict[str, Any]] = []
    check = {(_d(c)).get("check_id"): _d(c) for c in checks}

    # Trade vs SIC (the comparison itself is the model's job in media.py; this row reports it).
    sic = "; ".join(f"{s.get('code')} {s.get('label') or ''}".strip() for s in company.get("sic") or []) or "none"
    trade = req.get("declared_trade") or ""
    t = check.get("trade")
    verdict = "not_given" if not trade else {"clear": "match", "flag": "differs"}.get((t or {}).get("status"), "unknown")
    rows.append({"item": "Trade", "proposal": trade or "—", "record": f"SIC {sic}", "verdict": verdict,
                 "note": "" if verdict in ("match", "not_given") else (t or {}).get("summary", "")})

    # Turnover vs size of last accounts.
    turnover = req.get("declared_turnover_gbp")
    last = company.get("last_accounts") or {}
    ltype = last.get("type") or ""
    band = ACCOUNT_LIMITS.get(ltype)
    record = (f"Last accounts {ltype.replace('-', ' ')}" + (f" to {last.get('made_up_to')}" if last.get("made_up_to") else "")
              ) if ltype else "No accounts filed yet"
    if turnover is None:
        v, note = "not_given", ""
    elif not band:
        v, note = "unknown", "The accounts type doesn't limit turnover, or there are no accounts."
    elif band[0] == "dormant":
        v, note = ("differs", "Dormant accounts mean no trading.") if turnover else ("match", "")
    elif turnover > band[1]:
        v, note = "differs", f"{band[0].capitalize()} accounts are for turnover up to £{band[1]:,}."
    else:
        v, note = "match", f"Within the {band[0]} limit (£{band[1]:,})."
    rows.append({"item": "Turnover", "proposal": f"£{turnover:,}" if turnover is not None else "—",
                 "record": record, "verdict": v, "note": note})

    # Years trading vs incorporation.
    declared_years = req.get("declared_years_trading")
    yrs = _years(company.get("incorporated") or "", today)
    rec = f"Incorporated {company.get('incorporated')}" + (f" ({int(yrs)} years)" if yrs is not None else "") \
        if company.get("incorporated") else "Unknown"
    if declared_years is None:
        v, note = "not_given", ""
    elif yrs is None:
        v, note = "unknown", ""
    elif declared_years > yrs + 1:
        v, note = "differs", ("Trading longer than this company has existed: the business may have run through an "
                              "earlier company. Ask which.")
    else:
        v, note = "match", ""
    rows.append({"item": "Years trading", "proposal": str(declared_years) if declared_years is not None else "—",
                 "record": rec, "verdict": v, "note": note})

    # Directors named on the proposal vs current directors.
    declared = [n.strip() for n in re.split(r"[,;\n]+", req.get("declared_directors") or "") if n.strip()]
    on_record = company.get("officer_names") or [o.get("name", "") for o in company.get("officers") or []]
    if not declared:
        v, note = "not_given", ""
    elif not company.get("officers_read", True):
        v, note = "unknown", "Couldn't read the officer list."
    else:
        rec_t = [_tokens(n) for n in on_record]
        missing = [n for n in declared if not any(_same_person(_tokens(n), r) for r in rec_t)]
        extra = [n for n, r in zip(on_record, rec_t) if not any(_same_person(_tokens(d), r) for d in declared)]
        parts = []
        if missing:
            parts.append("Not on the register: " + ", ".join(missing) + ".")
        if extra:
            parts.append("On the register but not on the proposal: " + ", ".join(extra[:5]) + ".")
        v, note = ("differs" if parts else "match"), " ".join(parts)
    rows.append({"item": "Directors", "proposal": ", ".join(declared) or "—",
                 "record": f"{len(on_record)} current" + (": " + ", ".join(on_record[:4]) if on_record else ""),
                 "verdict": v, "note": note})
    return rows


# ---------------------------------------------------------------------------
# File note
# ---------------------------------------------------------------------------

ROUTE_WORDS = {"note": "Note only", "refer": "Refer", "outside_appetite": "Outside appetite",
               "compliance_hold": "Compliance hold"}
STATUS_WORDS = {"clear": "clear", "info": "info", "flag": "FLAG", "couldnt_check": "COULDN'T CHECK",
                "not_applicable": "n/a"}


def file_note(report: dict[str, Any]) -> str:
    """Plain text for the underwriting system. Short lines, no markdown."""
    r = report
    when = str(r.get("created_at", ""))[:16].replace("T", " ")
    lines = [
        f"PRE-SCREEN: {r.get('company_name')} ({r.get('company_number')})",
        f"Run {when} UTC · report {r.get('report_id')} · rules {r.get('rules_version')}",
    ]
    if (r.get("meta") or {}).get("data") == "demo":
        lines.append("DEMO DATA: not a real assessment.")
    lines += ["", f"Recommended route: {ROUTE_WORDS.get(r.get('route'), r.get('route'))}"]
    for reason in (r.get("route_reasons") or [])[:6]:
        lines.append(f"- {reason}")

    checks = r.get("checks") or []
    lines += ["", "Checks by group:"]
    for g in GROUPS:
        cs = [c for c in checks if (c.get("group") or group_of(c.get("check_id", ""))) == g["id"]]
        if not cs:
            continue
        lines.append(f"{g['title']}:")
        for c in cs:
            lines.append(f"  [{STATUS_WORDS.get(c.get('status'), c.get('status'))}] {c.get('title')}"
                         + (f": {c.get('not_checked_reason')}" if c.get("status") == "couldnt_check"
                            and c.get("not_checked_reason") else ""))
            for f in c.get("findings") or []:
                if f.get("route") != "note" or c.get("status") == "flag":
                    tags = f.get("tags") or fraud_tags(f.get("rule_id", ""))
                    lines.append(f"      - {f.get('text')}" + (f" [fraud indicator: {', '.join(tags)}]" if tags else ""))

    unchecked = r.get("couldnt_check") or []
    if unchecked:
        lines += ["", "Not checked (absence of a finding here is not a clear): " + ", ".join(unchecked) + "."]

    rows = [x for x in (r.get("proposal_vs_record") or []) if x.get("verdict") == "differs"]
    if rows:
        lines += ["", "Proposal differs from the public record:"]
        lines += [f"- {x['item']}: proposal {x['proposal']}; record {x['record']}. {x.get('note', '')}".rstrip()
                  for x in rows]

    qs = r.get("broker_questions") or []
    if qs:
        lines += ["", "Questions for the broker:"]
        lines += [f"{i}. {q['question']}" for i, q in enumerate(qs, 1)]
    if any(f.get("rule_id") in COMPLIANCE_ONLY for c in checks for f in c.get("findings") or []):
        lines += ["", "Sanctions: referred to compliance. Not raised with the broker."]

    decisions = r.get("decisions") or []
    if decisions:
        d = decisions[-1]
        lines += ["", f"Decision: {d.get('decision')}" + (f" by {d.get('underwriter')}" if d.get("underwriter") else "")
                  + f", {str(d.get('recorded_at', ''))[:16].replace('T', ' ')}"
                  + (" (differs from the recommendation)" if d.get("overrides_route") else ""),
                  f"Reason: {d.get('reason')}"]
    lines += ["", "A recommendation from public records only. The underwriter decides."]
    return "\n".join(lines)
