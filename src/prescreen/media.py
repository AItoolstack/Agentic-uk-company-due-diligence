"""
Adverse media: the only check that uses a model.

The model gets the company's identity facts (number, incorporation date,
previous names, town, directors) and decides, per search result, whether the
article is about *this* legal entity. That is the Wilko lesson: the old app
read news about the collapsed retailer as news about the company that later
took its name.

A deterministic guard runs first: an article dated before the company took
its current name can't be about this company under that name.

Media findings can refer a case; they can never make it outside appetite.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from src.prescreen.checks import CompanyData, _parse_date, active_officers
from src.prescreen.llm import JSONChat, LLMError
from src.prescreen.models import CheckResult, CheckStatus, Finding, MediaItem, Route, SourceRef
from src.prescreen.sources.http import SourceError

CATEGORIES = [
    "insolvency_or_distress", "fraud_or_financial_crime", "regulatory_action", "health_and_safety",
    "environmental", "litigation", "operational_incident", "sanctions", "neutral",
]

SYSTEM = """You screen search results for a UK commercial insurance underwriter.
For each result decide:
- about_this_company: "yes" only if the article is clearly about the exact legal entity described
  (same business, same period). "no" if it is about another company, a predecessor that used the
  same brand, a namesake, or a person with no link. "unsure" otherwise.
- category: one of CATEGORIES.
- severity: "high" (insolvency, fraud, criminal or regulatory penalty, serious injury or death,
  major pollution), "medium" (investigation, lawsuit, warning, significant incident), "low" (neutral,
  routine, positive, or only a passing mention).
- reason: one short sentence in plain English, saying what the article reports and why it is or
  isn't about this company.
Base everything on the title, outlet, date and snippet given. Do not invent facts.
Reply with JSON only: {"items": [{"i": <index>, "about_this_company": "...", "category": "...",
"severity": "...", "reason": "..."}], "trade_consistency": {"verdict": "consistent|inconsistent|unclear|not_asked",
"reason": "..."}}""".replace("CATEGORIES", ", ".join(CATEGORIES))


def _published_date(value: str) -> date | None:
    if not value:
        return None
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T", value)  # GDELT 20261010T120000Z
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    d = _parse_date(value)
    if d:
        return d
    try:
        return datetime.strptime(value.strip(), "%d %B %Y").date()
    except ValueError:
        return None


def current_name_since(data: CompanyData) -> date | None:
    prev = [p for p in data.profile.get("previous_company_names") or [] if p.get("ceased_on")]
    dates = [d for d in (_parse_date(p["ceased_on"]) for p in prev) if d]
    return max(dates) if dates else None


def identity_facts(data: CompanyData, declared_trade: str) -> dict[str, Any]:
    p = data.profile
    addr = p.get("registered_office_address") or {}
    return {
        "registered_name": data.name,
        "company_number": data.number,
        "incorporated": p.get("date_of_creation"),
        "current_name_since": str(current_name_since(data) or p.get("date_of_creation")),
        "previous_names": [f"{x.get('name')} (until {x.get('ceased_on')})" for x in p.get("previous_company_names") or []],
        "registered_office_town": addr.get("locality", ""),
        "status": p.get("company_status"),
        "sic_codes": p.get("sic_codes") or [],
        "active_directors": [o.get("name") for o in active_officers(data)][:10],
        "declared_trade": declared_trade or "(not given)",
    }


async def check_media(data: CompanyData, results: list[dict[str, str]] | SourceError, llm: JSONChat,
                      declared_trade: str = "", provider: str = "") -> tuple[CheckResult, CheckResult | None,
                                                                           list[MediaItem]]:
    title = "Adverse media"
    if isinstance(results, SourceError):
        return (CheckResult(check_id="media", title=title, status=CheckStatus.COULDNT_CHECK,
                            summary=f"Couldn't check: {results.plain}", not_checked_reason=results.plain),
                None, [])
    if not results:
        return (CheckResult(check_id="media", title=title, status=CheckStatus.CLEAR,
                            summary=f"No news or web results for this name ({provider}).",
                            sources=[SourceRef(label=provider or "search")]),
                None, [])

    since = current_name_since(data)
    items = [MediaItem(title=r.get("title", ""), url=r.get("url", ""), published=r.get("published", ""),
                       source=r.get("source", "")) for r in results]
    pre_rename = set()
    for i, it in enumerate(items):
        d = _published_date(it.published)
        if since and d and d < since:
            pre_rename.add(i)
            it.about_this_company = "no"
            it.reason = (f"Published {d:%d %b %Y}, before this company took its current name "
                         f"({since:%d %b %Y}); about a different entity.")

    if not llm.available:
        return (CheckResult(check_id="media", title=title, status=CheckStatus.COULDNT_CHECK,
                            summary=f"{len(items)} results found but not reviewed: no model configured.",
                            not_checked_reason="no model configured"), None, items)

    to_review = [{"i": i, "title": it.title, "outlet": it.source, "published": it.published,
                  "snippet": results[i].get("snippet", "")[:240]}
                 for i, it in enumerate(items) if i not in pre_rename]
    user = json.dumps({"company": identity_facts(data, declared_trade), "results": to_review}, ensure_ascii=False)
    try:
        answer = await llm.complete_json(SYSTEM, user) if to_review else {"items": []}
    except LLMError as err:
        return (CheckResult(check_id="media", title=title, status=CheckStatus.COULDNT_CHECK,
                            summary=f"{len(items)} results found but the review failed ({err}).",
                            not_checked_reason=f"model review failed ({err})"), None, items)

    for row in answer.get("items") or []:
        try:
            i = int(row.get("i"))
        except (TypeError, ValueError):
            continue
        if i in pre_rename or not 0 <= i < len(items):
            continue
        it = items[i]
        it.about_this_company = row.get("about_this_company", "unsure") if row.get("about_this_company") in (
            "yes", "no", "unsure") else "unsure"
        it.category = row.get("category", "") if row.get("category") in CATEGORIES else "neutral"
        it.severity = row.get("severity", "low") if row.get("severity") in ("low", "medium", "high") else "low"
        it.reason = str(row.get("reason", ""))[:300]

    findings: list[Finding] = []
    for it in items:
        if it.category in ("", "neutral") or it.severity == "low":
            continue
        src = [SourceRef(label=it.source or "article", url=it.url)]
        label = it.category.replace("_", " ")
        if it.about_this_company == "yes":
            findings.append(Finding(rule_id=f"media_{it.severity}", route=Route.REFER if it.severity == "high"
                                    else Route.NOTE,
                                    text=f"{label.capitalize()} ({it.published[:10] or 'undated'}): {it.title}",
                                    detail={"reason": it.reason}, sources=src))
        elif it.about_this_company == "unsure" and it.severity == "high":
            findings.append(Finding(rule_id="media_unsure", route=Route.NOTE,
                                    text=f"Possibly about this company, check: {it.title}",
                                    detail={"reason": it.reason}, sources=src))
    excluded = sum(1 for it in items if it.about_this_company == "no")
    summary_clear = (f"{len(items)} results reviewed ({provider}); nothing adverse about this company"
                     + (f"; {excluded} excluded as about a different entity." if excluded else "."))
    media = CheckResult(check_id="media", title=title, status=CheckStatus.CLEAR, summary=summary_clear,
                        findings=findings, sources=[SourceRef(label=provider or "search")])
    if any(f.route == Route.REFER for f in findings):
        media.status = CheckStatus.FLAG
        media.summary = "; ".join(f.text for f in findings if f.route == Route.REFER)
    elif findings:
        media.summary = summary_clear + " Notes: " + "; ".join(f.text for f in findings)

    trade = None
    tc = answer.get("trade_consistency") or {}
    if declared_trade:
        verdict = tc.get("verdict", "unclear")
        reason = str(tc.get("reason", ""))[:300]
        sics = ", ".join(data.profile.get("sic_codes") or []) or "none"
        if verdict == "inconsistent":
            trade = CheckResult(check_id="trade", title="Declared trade vs SIC codes", status=CheckStatus.FLAG,
                                summary=f"Declared '{declared_trade}' doesn't fit SIC {sics}: {reason}",
                                findings=[Finding(rule_id="trade_mismatch", route=Route.REFER,
                                                  text=f"Declared trade '{declared_trade}' doesn't match SIC {sics}. "
                                                       f"{reason}")])
        else:
            trade = CheckResult(check_id="trade", title="Declared trade vs SIC codes",
                                status=CheckStatus.CLEAR if verdict == "consistent" else CheckStatus.COULDNT_CHECK,
                                summary=f"SIC {sics} vs '{declared_trade}': {verdict}. {reason}".strip(),
                                not_checked_reason="" if verdict == "consistent" else "model couldn't tell")
    return media, trade, items
