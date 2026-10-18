"""
Rules: thresholds and how check results become one recommended route.

Thresholds marked DRAFT are judgement, not a cited rule. They are kept here,
in one place, so an underwriter can read and change them without touching
the checks. Every report records RULES_VERSION.
"""

from __future__ import annotations

from src.prescreen.models import CheckResult, CheckStatus, Route

RULES_VERSION = "draft-2026-10-19c"

THRESHOLDS = {
    # Company age
    "new_company_months": 12,          # DRAFT: under this -> note (new-venture question); refer if combined
    "young_company_months": 36,        # under this -> note
    # Filing
    "overdue_refer_days": 30,          # DRAFT: accounts overdue by more than this -> refer (1-30 days: note)
    "overdue_serious_days": 90,        # DRAFT: more than this counts as "serious" for the combination rule
    # Directors
    "director_lookback_years": 5,
    "director_insolvent_refer": 1,     # DRAFT: links to this many insolvent companies -> refer
    "director_insolvent_decline": 2,   # DRAFT: this many or more -> outside appetite
    "many_appointments": 20,           # DRAFT: directors with this many appointments are capped at refer
    "phoenix_young_months": 24,        # DRAFT: similar-name insolvent predecessor + company younger than this
    "max_directors_checked": 15,
    "max_dissolved_profiles_per_director": 8,
    "max_dissolved_profiles_total": 30,
    # Charges
    "charge_burst_lenders": 3,         # DRAFT: charges to this many different lenders ...
    "charge_burst_months": 12,         # ... within this many months -> refer
    # Gazette
    "gazette_recent_days": 183,        # DRAFT: a winding-up petition this recent -> outside appetite
    # FCA
    "fca_discipline_years": 5,
    # News
    "media_serious_years": 5,          # DRAFT: serious news older than this is a note, not a referral (Tesco 2017, live)
    # Names
    "rename_recent_months": 60,        # renames in the last 5 years are shown (they explain mismatched news)
}

# Which checks must run for a case to be "note" is set on each check (`critical=True`):
# status, filings, insolvency, both director checks and sanctions. If any of those
# couldn't be checked, the best a case can be is "refer".

DISTRESS_SIGNALS = {"charges_burst", "insolvency_history", "winding_up_petition", "gazette_insolvency_notice"}

# Once a company is closed or insolvent, these are consequences, not separate reasons (Carillion, live:
# "accounts 3032 days overdue" under "in liquidation"). They stay in the checks; they leave the reasons.
ENDED = {"company_closed", "company_insolvent"}
CONSEQUENCES_OF_ENDING = {"accounts_overdue", "accounts_overdue_serious", "accounts_late", "confirmation_overdue",
                          "no_psc", "psc_statement", "psc_none_statement", "dormant", "dormant_but_trading",
                          "accounts_size_mismatch", "strike_off_compulsory", "strike_off_unknown"}


def _surname(name: str) -> str:
    """'DE BRUNNER, Aidan Whiteman' -> 'De Brunner'."""
    return (name.split(",")[0] if "," in name else name.split(" ")[-1]).strip().title() or "a director"


def decide(checks: list[CheckResult]) -> tuple[Route, list[str]]:
    """Return the recommended route and the reasons, worst first."""
    route = Route.NOTE
    reasons: list[tuple[int, str]] = []
    ended = any(f.rule_id in ENDED for c in checks for f in c.findings)

    all_ids = {f.rule_id for c in checks for f in c.findings}
    directors = [f for c in checks for f in c.findings if f.rule_id == "director_insolvent_links"]
    for c in checks:
        for f in c.findings:
            if f.route.rank > route.rank:
                route = f.route
            if f.route.rank <= Route.NOTE.rank or (ended and f.rule_id in CONSEQUENCES_OF_ENDING):
                continue
            # Same fact twice (Wilko, live): "dormant but trading" already says the SIC doesn't fit.
            if f.rule_id == "trade_mismatch" and "dormant_but_trading" in all_ids:
                continue
            # Gazette notices are evidence for an insolvency already on record, not new reasons (BrewDog, live).
            if f.rule_id == "gazette_insolvency_notice" and all_ids & {"insolvency_history", "company_insolvent"}:
                continue
            # Several directors with insolvent companies are one reason, not a line each (Thames Water, live).
            if f.rule_id == "director_insolvent_links" and len(directors) > 1:
                continue
            reasons.append((f.route.rank, f.text))
        if c.status == CheckStatus.COULDNT_CHECK and c.critical:
            reasons.append((Route.REFER.rank,
                            f"{c.title} couldn't be checked: {c.not_checked_reason.rstrip('.')}."))
            if route.rank < Route.REFER.rank:
                route = Route.REFER
    if len(directors) > 1:
        top = max(directors, key=lambda f: f.route.rank)
        total = sum(int(f.detail.get("count", 0)) for f in directors)
        who = ", ".join(f"{_surname(f.detail.get('name', ''))} ({f.detail.get('count', '?')})"
                        for f in sorted(directors, key=lambda f: -int(f.detail.get("count", 0))))
        reasons.append((top.route.rank, f"{len(directors)} directors were directors of companies that became "
                                        f"insolvent in the last {THRESHOLDS['director_lookback_years']} years "
                                        f"({total} in all): {who}. Details under Directors: other companies."))

    # Combination rule (DRAFT): seriously overdue accounts plus another distress
    # signal is outside appetite even though each alone is a referral.
    rule_ids = {f.rule_id for c in checks for f in c.findings}
    if "accounts_overdue_serious" in rule_ids and rule_ids & DISTRESS_SIGNALS:
        if route.rank < Route.OUTSIDE_APPETITE.rank:
            route = Route.OUTSIDE_APPETITE
        if not ended:
            reasons.append((Route.OUTSIDE_APPETITE.rank,
                            f"Accounts more than {THRESHOLDS['overdue_serious_days']} days overdue together with "
                            "another distress signal."))
    # A new company is only a note on its own; with a director's insolvency history it refers.
    if "company_new" in rule_ids and "director_insolvent_links" in rule_ids and route.rank < Route.REFER.rank:
        route = Route.REFER
        reasons.append((Route.REFER.rank, "New company and a director linked to an insolvent company."))

    ordered = [text for _, text in sorted(reasons, key=lambda r: -r[0])]
    return route, list(dict.fromkeys(ordered))
