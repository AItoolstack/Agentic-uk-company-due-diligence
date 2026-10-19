"""
The guardrail: every specialist's output is checked before anyone sees it.

Four rules, in code, each one a way an agent could undo what the pre-screen gets right:

  1. couldnt_check_as_clear  A sentence says a check that couldn't run found nothing ("no CCJs", "news is
                             clean"). The coverage invariant is the product; prose must not break it.
  2. route_changed           Names a route other than the one the rules gave, or recommends a decision
                             ("safe to quote", "should be declined"). Agents may explain the route, not move it.
  3. sanctions_leak          Anything about sanctions in text meant for the broker, any broker text at all on a
                             compliance hold, or list detail in internal text on a hold.
  4. unsupported_fact        A company number, amount, date, year or percentage that isn't in the evidence the
                             agent was given. Cheap, exact, and catches the commonest invention.

Then an optional model judge (rule 5, `unsupported_claim`) reads the output against the evidence for claims
the patterns can't see, such as an allegation stated as fact. If the judge can't run, the result says so:
"checked by rules only" is shown, never silently treated as a full check. Same rule as the pre-screen.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Words that tie a sentence to a check, by check_id.
CHECK_WORDS: dict[str, list[str]] = {
    "status": ["company status", "status"],
    "age": ["incorporat", "years trading", "how old"],
    "ownership": ["owner", "psc", "significant control", "shareholder"],
    "trade": ["trade", "sic", "business activity", "what it does"],
    "filings": ["accounts", "filing", "confirmation statement"],
    "insolvency": ["insolven", "liquidat", "administration", "winding"],
    "charges": ["charge", "lender", "borrow", "secured"],
    "ccj": ["ccj", "county court", "judgment", "judgement"],
    "directors_disqualified": ["disqualif"],
    "directors_insolvency": ["director"],
    "changes": ["board change", "ownership change", "appointed", "resigned", "appointment"],
    "sanctions": ["sanction"],
    "fca": ["fca", "authoris", "authoriz", "regulator"],
    "media": ["news", "media", "press", "adverse", "coverage"],
}

CLEAR = re.compile(
    r"\b(clear|clean|no (adverse|issues?|concerns?|problems?|matches?|findings?|records?|red flags?|ccjs?|"
    r"judgments?|judgements?|charges?|disqualifications?|news|coverage)|nothing (adverse|found|of concern|to report|"
    r"untoward)|passed|satisfactory|in good standing|in order|all fine|no sign)\b", re.I)
NEGATED = re.compile(
    r"(couldn'?t|could not|cannot|can'?t|unable|not (been |yet )?(checked|read|reviewed|looked|run|available)|"
    r"wasn'?t (checked|read|run)|weren'?t (checked|read)|unknown|didn'?t run|did not run|failed|gap|unchecked|"
    r"not a clear result|no data|not possible)", re.I)
ALL_CLEAR = re.compile(r"\b(all|every) (the )?checks? (are |were |came back )?(clear|clean|fine|passed)|"
                       r"\bnothing of concern\b|\bfully checked\b|\bno (issues|concerns) (at all|anywhere)\b", re.I)

DECIDING = re.compile(
    r"\b(i|we) (would )?(recommend|suggest|advise)( that)?( you| the underwriter)? (proceed|quote|bind|accept|decline|"
    r"offer terms)|\b(safe|fine|okay|ok) to (quote|bind|proceed|write|offer)\b|\b(approve|accept) (this|the) "
    r"(risk|case|proposal)\b|\bshould (be )?(declined|accepted|quoted|bound|written)\b|\b(go ahead|green light)\b",
    re.I)
ROUTE_WORDS: dict[str, re.Pattern[str]] = {
    "note": re.compile(r"\bno referral (is )?(needed|required)\b|\bnote only\b|\bno (need|reason) to refer\b", re.I),
    "refer": re.compile(r"\b(needs?|requires?) (a )?referral\b|\bshould be referred\b|\broute[ds]? (is |as )?refer\b",
                        re.I),
    "outside_appetite": re.compile(r"\boutside (of )?(our |the )?appetite\b|\bdecline (the|this) (case|risk|quote|"
                                   r"cover|proposal)\b", re.I),
    "compliance_hold": re.compile(r"\bcompliance hold\b", re.I),
}

SANCTIONS_EXTERNAL = re.compile(
    r"sanction|\bofsi\b|designat|asset.freez|frozen asset|hm treasury|money.launder|\baml\b|"
    r"compliance (hold|team|review|check|decision)|\bpep\b|politically exposed", re.I)
SANCTIONS_INTERNAL_DETAIL = re.compile(r"\bregime\b|\bofsi\b|list(ed)? (entry|id|name)|designated (person|entity|"
                                       r"under|as)|\bunique id\b", re.I)

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
NUMBER = re.compile(r"\b(?:[A-Z]{2}\d{6}|\d{8})\b")
MONEY = re.compile(r"£\s?(\d[\d,]*(?:\.\d+)?)\s?(m|bn|k|million|billion)?\b", re.I)
PERCENT = re.compile(r"\b(\d+(?:\.\d+)?)\s?%")
DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2}) (" + "|".join(MONTHS) + r"|" + "|".join(m[:3] for m in MONTHS) +
                            r")\.? (\d{4})\b", re.I)
MONTH_YEAR = re.compile(r"\b(" + "|".join(MONTHS) + r"|" + "|".join(m[:3] for m in MONTHS) + r")\.? (\d{4})\b",
                        re.I)
YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _v(rule: str, detail: str, quote: str = "") -> dict[str, str]:
    return {"rule": rule, "detail": detail, "quote": quote[:240]}


def check_clear_claims(text: str, gaps: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    gap_ids = [g.get("check_id", "") for g in gaps]
    for s in sentences(text):
        if NEGATED.search(s):
            continue
        if gaps and ALL_CLEAR.search(s):
            out.append(_v("couldnt_check_as_clear", "says everything was clear, but some checks couldn't run", s))
            continue
        if not CLEAR.search(s):
            continue
        low = s.lower()
        for gid in gap_ids:
            if any(w in low for w in CHECK_WORDS.get(gid, [])):
                title = next((g.get("check", gid) for g in gaps if g.get("check_id") == gid), gid)
                out.append(_v("couldnt_check_as_clear", f"reads as clear, but '{title}' couldn't be checked", s))
                break
    return out


def check_route(text: str, route: str) -> list[dict[str, str]]:
    out = []
    for s in sentences(text):
        if DECIDING.search(s):
            out.append(_v("route_changed", "recommends a decision; only a named underwriter decides", s))
            continue
        for other, pattern in ROUTE_WORDS.items():
            if other != route and pattern.search(s):
                out.append(_v("route_changed", f"names the route '{other}', but the rules gave '{route}'", s))
                break
    return out


def check_sanctions(text: str, audience: str, route: str) -> list[dict[str, str]]:
    if audience == "external":
        if route == "compliance_hold" and text.strip():
            return [_v("sanctions_leak", "nothing may go to the broker while a sanctions match is open")]
        out = []
        for s in sentences(text):
            if SANCTIONS_EXTERNAL.search(s):
                out.append(_v("sanctions_leak", "mentions sanctions or compliance in text for the broker", s))
        return out
    if route == "compliance_hold":
        return [_v("sanctions_leak", "gives sanctions list detail; on a hold that is for compliance only", s)
                for s in sentences(text) if SANCTIONS_INTERNAL_DETAIL.search(s)]
    return []


def _norm_money(digits: str, unit: str) -> set[str]:
    raw = digits.replace(",", "")
    forms = {raw, digits}
    try:
        value = float(raw)
    except ValueError:
        return forms
    mult = {"k": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}.get((unit or "").lower(), 1)
    full = value * mult
    if full == int(full):
        forms |= {str(int(full)), f"{int(full):,}"}
    return forms


def check_facts(text: str, evidence: str) -> list[dict[str, str]]:
    """Every exact fact in the output must be found in what the agent was given."""
    ev = evidence.lower()
    ev_digits = re.sub(r"[,\s]", "", ev)
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def miss(kind: str, token: str, s: str) -> None:
        if token not in seen:
            seen.add(token)
            out.append(_v("unsupported_fact", f"{kind} '{token}' isn't in the evidence this agent was given", s))

    for s in sentences(text):
        for m in NUMBER.finditer(s):
            if m.group(0).lower() not in ev:
                miss("company number", m.group(0), s)
        for m in MONEY.finditer(s):
            if not any(f.lower() in ev or f.replace(",", "") in ev_digits for f in _norm_money(m.group(1), m.group(2))):
                miss("amount", m.group(0).strip(), s)
        for m in PERCENT.finditer(s):
            if m.group(1) not in ev:
                miss("percentage", m.group(0), s)
        consumed: list[tuple[int, int]] = []
        for m in DAY_MONTH_YEAR.finditer(s):
            consumed.append(m.span())
            day, mon, year = int(m.group(1)), m.group(2).lower()[:3], m.group(3)
            mm = next(i + 1 for i, x in enumerate(MONTHS) if x.startswith(mon))
            iso = f"{year}-{mm:02d}-{day:02d}"
            if iso not in ev and m.group(0).lower() not in ev:
                miss("date", m.group(0), s)
        for m in MONTH_YEAR.finditer(s):
            if any(a <= m.start() < b for a, b in consumed):
                continue
            consumed.append(m.span())
            mon, year = m.group(1).lower()[:3], m.group(2)
            mm = next(i + 1 for i, x in enumerate(MONTHS) if x.startswith(mon))
            if f"{year}-{mm:02d}" not in ev and f"{mon} {year}" not in ev and m.group(0).lower() not in ev:
                miss("date", m.group(0), s)
        for m in YEAR.finditer(s):
            if any(a <= m.start() < b for a, b in consumed):
                continue
            if m.group(0) not in ev:
                miss("year", m.group(0), s)
    return out


JUDGE_SYSTEM = """You check a short piece of writing by an AI assistant against the evidence it was given.
The writing is for a commercial insurance underwriter (or, if marked external, for an insurance broker).
List only real problems:
- "unsupported": a claim of fact that the evidence doesn't support (names, events, roles, outcomes, causes).
- "allegation_as_fact": something the evidence reports as alleged, claimed, charged or under investigation that
  the writing states as established.
Don't flag wording, tone, or reasonable summaries of what the evidence says. Don't flag statements that
something couldn't be checked.
Reply as JSON: {"problems": [{"kind": "unsupported" | "allegation_as_fact", "quote": "<the words>",
"why": "<one short sentence>"}]}. Use an empty list when there are none."""


async def judge(model: Any, agent: str, text: str, evidence: str, audience: str) -> tuple[list[dict[str, str]], str]:
    """Returns (violations, status). status: "ran" | "off" | "unavailable: <reason>"."""
    if model is None:
        return [], "off"
    user = (f"Audience: {audience}.\n\nEVIDENCE (JSON from the pre-screen's tools):\n{evidence[:24000]}\n\n"
            f"WRITING TO CHECK:\n{text[:6000]}")
    try:
        out = await model.json(f"guardrail:{agent}", JUDGE_SYSTEM, user, purpose="judge", max_output_tokens=1200)
    except Exception as err:  # noqa: BLE001 - a judge outage must be visible, not fatal
        return [], f"unavailable: {str(err)[:120]}"
    problems = out.get("problems") if isinstance(out, dict) else None
    if not isinstance(problems, list):
        return [], "unavailable: the judge's reply had no problem list"
    rule = {"unsupported": "unsupported_claim", "allegation_as_fact": "allegation_as_fact"}
    return [_v(rule.get(str(p.get("kind")), "unsupported_claim"), str(p.get("why", ""))[:200], str(p.get("quote", "")))
            for p in problems if isinstance(p, dict) and p.get("quote")][:8], "ran"


async def check(text: str, *, agent: str, audience: str, summary: dict[str, Any], evidence: str,
                judge_model: Any = None) -> dict[str, Any]:
    """Run every rule. `summary` is the MCP summary of the case (route, couldnt_check)."""
    route = summary.get("route", "")
    gaps = summary.get("couldnt_check") or []
    rules = (check_sanctions(text, audience, route) + check_route(text, route) + check_clear_claims(text, gaps)
             + check_facts(text, evidence))
    judged, judge_status = ([], "skipped: rules already block it") if rules else \
        await judge(judge_model, agent, text, evidence, audience)
    violations = rules + judged
    return {"passed": not violations, "violations": violations, "judge": judge_status,
            "checked_by": "rules + model judge" if judge_status == "ran" else "rules only"}


def judge_enabled() -> bool:
    return os.environ.get("AGENT_GUARDRAIL_JUDGE", "on").lower() not in ("off", "0", "false", "no")
