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

from src.prescreen.checks import CompanyData, _parse_date, _years_ago, active_officers
from src.prescreen.rules import THRESHOLDS as T
from src.prescreen.llm import JSONChat, LLMError
from src.prescreen.models import CheckResult, CheckStatus, Finding, MediaItem, Route, SourceRef
from src.prescreen.sources.http import SourceError
from src.prescreen.sic import label as sic_label
from src.prescreen.sources.news import short_name

CATEGORIES = [
    "insolvency_or_distress", "fraud_or_financial_crime", "regulatory_action", "health_and_safety",
    "environmental", "litigation", "operational_incident", "sanctions", "neutral",
]

SYSTEM = """You screen search results for a UK commercial insurance underwriter.
For each result decide:
- about_this_company: "yes" only if the article is clearly about the exact legal entity described
  (same business, same period). "related" if it is about a different legal entity in the same group or
  brand family: a parent, subsidiary or sister company, including an overseas one (for Revolut Ltd, a
  fine on Revolut Bank UAB is "related"; for Monzo Bank Limited, Monzo Bank Europe DAC is "related").
  "no" if it is about another company, a predecessor that used the same brand, a namesake, or a person
  with no link. "unsure" otherwise. When a regulator acts against an auditor, adviser, lender or supplier over
  its work for this company (KPMG fined over its Carillion audits), the article is "no": the other firm was
  acted against, and entity_named is that firm.
- entity_named: the legal entity the article names as the subject (e.g. "Revolut Bank UAB"), exactly as
  written; "" if the article only uses the brand. For a fine, penalty or enforcement action, name the
  entity that was fined or acted against.
- event_year: the year the main event the article reports actually happened, when the title or snippet makes it
  clear (a fine imposed in 2017 discussed in a 2024 article is 2017); null if unclear.
- category: one of CATEGORIES.
- severity: "high" (insolvency, fraud, criminal or regulatory penalty, serious injury or death,
  major pollution), "medium" (investigation, lawsuit, warning, significant incident), "low" (neutral,
  routine, positive, or only a passing mention). If the company is only the victim, the place where it
  happened or a bystander (a shoplifter caught in its store, a fraud committed against it), severity is "low".
  A licence, authorisation or registration being granted is "neutral", severity "low".
- reason: one short sentence in plain English, saying what the article reports and why it is or
  isn't about this company.
Base everything on the title, outlet, date and snippet given. Do not invent facts.
trade_consistency: when declared_trade is given, answer "consistent", "inconsistent" or "unclear" by comparing it
with the SIC codes and their labels, reading the labels literally (don't assume a code covers more than its label
says); use "not_asked" only when declared_trade is "(not given)". A dormant SIC (99999) with a
trading business declared is "inconsistent".
Reply with JSON only: {"items": [{"i": <index>, "about_this_company": "...", "entity_named": "...", "event_year": <year or null>, "category": "...",
"severity": "...", "reason": "..."}], "trade_consistency": {"verdict": "consistent|inconsistent|unclear|not_asked",
"reason": "..."}}""".replace("CATEGORIES", ", ".join(CATEGORIES))


_UK_FORMS = r"\b(limited|ltd|plc|llp|public limited company|the)\b"


def _norm_entity(name: str) -> str:
    s = re.sub(r"['\u2019]s\b", "", (name or "").lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(_UK_FORMS, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def entity_relation(entity_named: str, registered_name: str, previous_names: list[str]) -> str:
    """Code check on the model's reading. Returns "exact", "related" or "" (can't tell from the name).

    "Revolut Bank UAB" against REVOLUT LTD is related: it starts with the brand but adds words the
    registered name doesn't have ("Bank UAB"). A brand alone ("Monzo") can't be told apart by name,
    so the model's answer stands."""
    named = _norm_entity(entity_named)
    if not named:
        return ""
    own = {_norm_entity(registered_name)} | {_norm_entity(n) for n in previous_names}
    if named in own:
        return "exact"
    brand = _norm_entity(short_name(registered_name))
    if brand and named.startswith(brand + " "):
        return "related"
    return ""


def names_this_entity(entity_named: str, registered_name: str, previous_names: list[str]) -> bool:
    """Stricter than entity_relation: the article names this entity *with its legal form* ("Revolut Ltd"),
    not just the brand ("Revolut"), which for REVOLUT LTD normalises to the same thing."""
    return (entity_relation(entity_named, registered_name, previous_names) == "exact"
            and re.search(_UK_FORMS.replace("|the", ""), (entity_named or "").lower()) is not None)


def _with_forms(text: str) -> str:
    s = re.sub(r"['\u2019]s\b", "", (text or "").lower())
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\bpublic limited company\b", "plc", s)
    s = re.sub(r"\blimited\b", "ltd", s)
    return " " + re.sub(r"\s+", " ", s).strip() + " "


def entity_in_text(entity_named: str, text: str) -> bool:
    """Rules 20g. The model's claim that an article names this exact entity has to be checkable: the entity, with
    its legal form, must appear in the title or snippet the model was shown (Revolut, live 20 Oct: "Revolut Fined
    €3.5 Million for AML Failures" came back as naming "Revolut Ltd"; the fine was on Revolut Bank UAB)."""
    named = _with_forms(entity_named).strip()
    return bool(named) and f" {named} " in _with_forms(text)


_LICENCE_GRANTED = re.compile(r"\b(wins?|won|receives?|received|granted|secures?|secured|gets|awarded|obtains?|"
                              r"obtained|approved for)\b.{0,60}\b(licen[cs]e|authori[sz]ation|approval|permission|"
                              r"registration|charter)\b|\b(licen[cs]e|authori[sz]ation|approval)\b.{0,30}\b(granted|"
                              r"approved)\b", re.I)
_FIRM_WORDS = re.compile(r"\b(ltd|limited|plc|llp|llc|inc|corp|gmbh|ag|sa|nv|bv|uab|dac|bank|group|holdings|partners|"
                         r"kpmg|deloitte|pwc|pricewaterhousecoopers|ernst|ey|grant thornton|bdo|mazars|forvis)\b|&", re.I)


_AUDITOR_FINED = re.compile(r"\b(KPMG|Deloitte|PwC|PricewaterhouseCoopers|EY|Ernst & Young|Grant Thornton|BDO|Mazars|"
                            r"Forvis Mazars)\b(?=.{0,80}\b(fine|fined|fines|penalty|sanction|sanctioned)\b)", re.I)


def _looks_like_a_firm(name: str) -> bool:
    """An organisation, not a person: a person named in news (a former director fined) stays with the director checks."""
    return bool(name) and _FIRM_WORDS.search(name) is not None


_LICENCE_LOST = re.compile(r"\b(revok|suspend|refus|withdr|cancel|lose|lost|strip|den(y|ied))", re.I)


def in_group(data: CompanyData) -> bool:
    """A corporate owner on the PSC register means other companies share the brand."""
    items = data.pscs.get("items") if isinstance(data.pscs, dict) else []
    return any("corporate" in (p.get("kind") or "") and not p.get("ceased_on") for p in items or [])


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
        # Codes with their official labels: given bare numbers, the model guessed 62090 ("Other information
        # technology service activities") covered e-money and passed Revolut's trade as consistent.
        "sic_codes": [f"{c} {sic_label(c)}".strip() if sic_label(c) else f"{c} (label not known: say unclear)"
                      for c in p.get("sic_codes") or []],
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
                await _trade_only(data, llm, declared_trade), [])
    if not results:
        return (CheckResult(check_id="media", title=title, status=CheckStatus.CLEAR,
                            summary=f"No news or web results for this name ({provider}).",
                            sources=[SourceRef(label=provider or "search")]),
                await _trade_only(data, llm, declared_trade), [])

    since = current_name_since(data)
    old_names = [short_name(p.get("name", "")).lower() for p in data.profile.get("previous_company_names") or []]
    items = [MediaItem(title=r.get("title", ""), url=r.get("url", ""), published=r.get("published", ""),
                       source=r.get("source", "")) for r in results]
    pre_rename = set()
    for i, it in enumerate(items):
        d = _published_date(it.published)
        mentions_old = any(o and o in f"{it.title} {results[i].get('snippet', '')}".lower() for o in old_names)
        if since and d and d < since and not mentions_old:
            pre_rename.add(i)
            it.about_this_company = "no"
            it.reason = (f"Published {d:%d %b %Y}, before this company took its current name "
                         f"({since:%d %b %Y}); about a different entity.")

    if not llm.available:
        return (CheckResult(check_id="media", title=title, status=CheckStatus.COULDNT_CHECK,
                            summary=f"{len(items)} results found but not reviewed: no model configured.",
                            not_checked_reason="no model configured"),
                _trade_unchecked(data, declared_trade, "no model configured"), items)

    to_review = [{"i": i, "title": it.title, "outlet": it.source, "published": it.published,
                  "snippet": results[i].get("snippet", "")[:240]}
                 for i, it in enumerate(items) if i not in pre_rename]
    user = json.dumps({"company": identity_facts(data, declared_trade), "results": to_review}, ensure_ascii=False)
    if not to_review:
        answer: dict[str, Any] = {"items": []}
        trade_now = await _trade_only(data, llm, declared_trade)
    else:
        trade_now = None
    try:
        if to_review:
            answer = await llm.complete_json(SYSTEM, user)
    except LLMError as err:
        return (CheckResult(check_id="media", title=title, status=CheckStatus.COULDNT_CHECK,
                            summary=f"{len(items)} results found but the review failed ({err}).",
                            not_checked_reason=f"model review failed ({err})"),
                _trade_unchecked(data, declared_trade, f"model review failed ({err})"), items)

    for it in items:
        if it.about_this_company != "no":
            it.about_this_company = "not_reviewed"
    for row in answer.get("items") or []:
        try:
            i = int(row.get("i"))
        except (TypeError, ValueError):
            continue
        if i in pre_rename or not 0 <= i < len(items):
            continue
        it = items[i]
        it.about_this_company = row.get("about_this_company", "unsure") if row.get("about_this_company") in (
            "yes", "related", "no", "unsure") else "unsure"
        it.entity_named = str(row.get("entity_named") or "")[:120]
        it.category = row.get("category", "") if row.get("category") in CATEGORIES else "neutral"
        it.severity = row.get("severity", "low") if row.get("severity") in ("low", "medium", "high") else "low"
        it.reason = str(row.get("reason", ""))[:300]
        try:
            y = int(row.get("event_year")) if row.get("event_year") not in (None, "") else None
            it.event_year = y if y and 1900 < y <= data.today.year else None
        except (TypeError, ValueError):
            it.event_year = None

    # Guard (rules 20h; Carillion, live): "KPMG ... is fined record £21m" over its Carillion audits was read as a fine
    # on Carillion. When the model names a different firm as the one acted against (no brand word in it), the article
    # is about that firm. And a licence or authorisation being granted is routine, whatever severity came back.
    prev_names_all = [p.get("name", "") for p in data.profile.get("previous_company_names") or []]
    brand_words = set(_norm_entity(short_name(data.name)).split()) - {"group", "holdings", "uk", "bank", "plc"}
    for i, it in enumerate(items):
        auditor = _AUDITOR_FINED.search(it.title or "")
        if auditor and it.category == "regulatory_action" and not brand_words & set(_norm_entity(auditor.group(1)).split()):
            it.entity_named = auditor.group(1)  # the headline says who was fined, whatever the model read
        if it.about_this_company in ("yes", "unsure") and _looks_like_a_firm(it.entity_named) and brand_words \
                and not (brand_words & set(_norm_entity(it.entity_named).split())) \
                and not entity_relation(it.entity_named, data.name, prev_names_all):
            it.about_this_company = "no"
            it.reason = (f"About {it.entity_named}, not this company. " + it.reason)[:300]
        if it.severity != "low" and _LICENCE_GRANTED.search(it.title) and not _LICENCE_LOST.search(it.title):
            it.severity, it.category = "low", "neutral"
            it.reason = ("A licence or authorisation being granted is routine. " + it.reason)[:300]

    # Guard (the Wilko trap, seen live): news of a collapse can't be about this legal entity when Companies
    # House shows it active with no insolvency record. It's usually a predecessor that used the brand.
    status = (data.profile.get("company_status") or "").lower()
    no_insolvency = not data.profile.get("has_insolvency_history") and "insolvency" not in (data.profile.get("links") or {})
    if status == "active" and no_insolvency:
        for it in items:
            if it.about_this_company == "yes" and it.category == "insolvency_or_distress":
                it.about_this_company = "unsure"
                it.reason = ("Reports a collapse or insolvency, but Companies House shows this company active with no "
                             "insolvency record; probably about another company that used the name. " + it.reason)[:300]

    # Guard (Revolut, live): a fine on Revolut Bank UAB was read as a fine on Revolut Ltd. When the model names
    # a different legal entity in the brand family, the article is related, not about this company, whatever
    # it answered. Related news is shown but never becomes a reason for the route.
    prev = [p.get("name", "") for p in data.profile.get("previous_company_names") or []]
    for it in items:
        if it.about_this_company in ("yes", "unsure") and entity_relation(it.entity_named, data.name, prev) == "related":
            it.about_this_company = "related"
            it.reason = (f"Names {it.entity_named}, a different legal entity from {data.name}. " + it.reason)[:300]

    # Guard (Revolut, live re-run): "Italy fines Revolut" names only the brand. In a group with regulated firms
    # in several countries, a regulator's action can't be pinned on this UK entity from the brand alone. When the
    # company is part of a group (a corporate owner on its PSC register) and the article doesn't name this exact
    # entity with its legal form, it's "may be about this company": shown, never a reason. A UK regulator's
    # action on this firm still reaches the route through the FCA check, from the Register itself.
    # Rules 20g: the model's "names this entity" must be visible in the text it was shown, else it counts as brand only.
    if in_group(data):
        for i, it in enumerate(items):
            if it.about_this_company != "yes" or it.category != "regulatory_action":
                continue
            shown = f"{it.title} {results[i].get('snippet', '')}"
            named = names_this_entity(it.entity_named, data.name, prev)
            if named and entity_in_text(it.entity_named, shown):
                continue
            it.about_this_company = "unsure"
            if named:
                it.reason = (f"The reading said it names {it.entity_named}, but the headline and snippet name only the "
                             "brand, so it can't be pinned on this company in the group. " + it.reason)[:300]
            else:
                it.reason = ("Names only the brand"
                             + (f" ({it.entity_named})" if it.entity_named else "")
                             + ", not which company in the group was acted against. " + it.reason)[:300]

    # One finding per severity, not one per article: seven articles about one collapse are one fact.
    findings: list[Finding] = []
    unreviewed = [it for it in items if it.about_this_company == "not_reviewed"]
    adverse = [it for it in items if it.about_this_company in ("yes", "unsure")
               and it.category not in ("", "neutral") and it.severity != "low"]
    # Serious news older than the look-back is history, not a referral (Tesco's 2017 accounting fine, live).
    old_cut = _years_ago(data.today, T["media_serious_years"]).isoformat()
    # An undated result is dated by the latest year its headline or snippet names (Tesco, live 20 Oct, rules 20h:
    # "sfo v tesco dpa 2017", undated, became a recent serious item and referred the case).
    snippets = {id(it): results[i].get("snippet", "") for i, it in enumerate(items)}

    def when(it: MediaItem) -> str:
        # A retrospective is dated by its event (Tesco, live 20 Oct: a 2024 law-firm piece on the 2014-17 accounting
        # scandal read as recent serious news). The event year only ever makes an item older, never newer.
        if it.event_year and (not it.published or str(it.event_year) < it.published[:4]):
            return f"{it.event_year}-12-31"
        if it.published:
            return it.published[:10]
        years = [int(y) for y in re.findall(r"\b(19[89]\d|20[0-4]\d)\b", f"{it.title} {snippets.get(id(it), '')}")]
        return f"{max(years)}-12-31" if years else ""
    old = {id(it) for it in adverse if it.severity == "high" and when(it) and when(it) < old_cut}
    for severity, route in (("high", Route.REFER), ("medium", Route.NOTE), ("old", Route.NOTE)):
        if severity == "old":
            group = [it for it in adverse if it.about_this_company == "yes" and id(it) in old]
        else:
            group = [it for it in adverse if it.about_this_company == "yes" and it.severity == severity
                     and id(it) not in old]
        if group and severity == "old":
            group.sort(key=lambda it: it.published or "", reverse=True)
            findings.append(Finding(
                rule_id="media_old", route=route,
                text=(f"{len(group)} serious news item{'s' if len(group) != 1 else ''} older than "
                      f"{T['media_serious_years']} years ({', '.join(sorted({it.category.replace('_', ' ') for it in group}))}); "
                      f"latest {group[0].published[:10]}: {group[0].title}"),
                detail={"titles": [it.title for it in group][:10],
                        "categories": sorted({it.category for it in group})},
                sources=[SourceRef(label=it.source or "article", url=it.url) for it in group[:3]]))
            continue
        if group:
            group.sort(key=lambda it: it.published or "", reverse=True)
            cats = sorted({it.category.replace("_", " ") for it in group})
            latest = group[0]
            findings.append(Finding(
                rule_id=f"media_{severity}", route=route,
                text=(f"{len(group)} {'serious ' if severity == 'high' else ''}adverse news item"
                      f"{'s' if len(group) != 1 else ''} about this company ({', '.join(cats)}); latest "
                      f"{latest.published[:10] or 'undated'}: {latest.title}"),
                detail={"titles": [it.title for it in group][:10],
                        "categories": sorted({it.category for it in group})},
                sources=[SourceRef(label=it.source or "article", url=it.url) for it in group[:3]]))
    unsure = [it for it in adverse if it.about_this_company == "unsure" and it.severity == "high"]
    if unsure:
        findings.append(Finding(
            rule_id="media_unsure", route=Route.NOTE,
            text=(f"{len(unsure)} serious news item{'s' if len(unsure) != 1 else ''} may be about this company; "
                  f"open and check: {unsure[0].title}"),
            sources=[SourceRef(label=it.source or "article", url=it.url) for it in unsure[:3]]))
    related = [it for it in items if it.about_this_company == "related"
               and it.category not in ("", "neutral") and it.severity != "low"]
    if related:
        related.sort(key=lambda it: it.published or "", reverse=True)
        brand = _norm_entity(short_name(data.name))
        names = sorted({it.entity_named for it in related
                        if it.entity_named and _norm_entity(it.entity_named) not in (brand, _norm_entity(data.name))})
        findings.append(Finding(
            rule_id="media_related", route=Route.NOTE,
            text=(f"{len(related)} adverse news item{'s' if len(related) != 1 else ''} about another company in the group"
                  + (f" ({', '.join(names[:3])})" if names else "")
                  + f", not this one; latest: {related[0].title}"),
            detail={"titles": [it.title for it in related][:10], "entities": names},
            sources=[SourceRef(label=it.source or "article", url=it.url) for it in related[:3]]))
    excluded = sum(1 for it in items if it.about_this_company == "no")
    summary_clear = (f"{len(items)} results reviewed ({provider}); nothing adverse about this company"
                     + (f"; {excluded} excluded as about a different entity." if excluded else "."))
    media = CheckResult(check_id="media", title=title, status=CheckStatus.CLEAR, summary=summary_clear,
                        findings=findings, sources=[SourceRef(label=provider or "search")])
    if any(f.route == Route.REFER for f in findings):
        media.status = CheckStatus.FLAG
        media.summary = " ".join(f.text for f in findings if f.route == Route.REFER)
    elif findings:
        # Never "nothing adverse" above an adverse finding (Greggs, live).
        media.status = CheckStatus.INFO
        media.summary = f"{len(items)} results reviewed ({provider}). " + " ".join(f.text for f in findings)
    if unreviewed:
        # Never call results clear that nobody (human or model) actually read.
        media.status = CheckStatus.COULDNT_CHECK
        media.not_checked_reason = f"{len(unreviewed)} of {len(items)} results weren't reviewed by the model"
        media.summary = f"Couldn't check: {media.not_checked_reason}. Open them below."
    if provider and "gdelt" in provider.lower():
        media.findings.append(Finding(rule_id="news_fallback", route=Route.NOTE,
                                      text=("The main news search failed, so results" if "fallback" in provider.lower()
                                            else "Results") + " come from GDELT, which only covers about the last "
                                           "3 months."))
        if media.status == CheckStatus.CLEAR:
            media.status = CheckStatus.INFO

    if not to_review:
        return media, trade_now, items
    return media, _trade_from(data, declared_trade, answer.get("trade_consistency") or {}), items


async def _trade_only(data: CompanyData, llm: JSONChat, declared_trade: str) -> CheckResult | None:
    """No articles to read, but a declared trade still needs comparing with the SIC codes.
    Same prompt with an empty result list, so it is still the run's one model call."""
    if not declared_trade:
        return None
    if not llm.available:
        return _trade_unchecked(data, declared_trade, "no model configured")
    user = json.dumps({"company": identity_facts(data, declared_trade), "results": []}, ensure_ascii=False)
    try:
        answer = await llm.complete_json(SYSTEM, user)
    except LLMError as err:
        return _trade_unchecked(data, declared_trade, f"model review failed ({err})")
    return _trade_from(data, declared_trade, answer.get("trade_consistency") or {})


def _trade_from(data: CompanyData, declared_trade: str, tc: dict[str, Any]) -> CheckResult | None:
    trade = None
    if declared_trade and not tc:
        trade = _trade_unchecked(data, declared_trade, "the model gave no answer on it")
    if declared_trade and tc:
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
    return trade


def _trade_unchecked(data: CompanyData, declared_trade: str, why: str) -> CheckResult | None:
    if not declared_trade:
        return None
    sics = ", ".join(data.profile.get("sic_codes") or []) or "none"
    return CheckResult(check_id="trade", title="Declared trade vs SIC codes", status=CheckStatus.COULDNT_CHECK,
                       summary=f"Couldn't compare '{declared_trade}' with SIC {sics}: {why}.",
                       not_checked_reason=why)
