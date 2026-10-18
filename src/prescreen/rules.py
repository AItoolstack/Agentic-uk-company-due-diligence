"""
Rules: thresholds and how check results become one recommended route.

Thresholds marked DRAFT are judgement, not a cited rule. They are kept here,
in one place, so an underwriter can read and change them without touching
the checks. Every report records RULES_VERSION.
"""

from __future__ import annotations

from src.prescreen.models import CheckResult, CheckStatus, Route

RULES_VERSION = "draft-2026-10-18"

THRESHOLDS = {
    # Company age
    "new_company_months": 12,          # DRAFT: under this -> refer
    "young_company_months": 36,        # DRAFT: under this -> note
    # Filing
    "overdue_serious_days": 90,        # DRAFT: accounts overdue by more than this counts as "serious"
    # Directors
    "director_lookback_years": 5,
    "director_insolvent_refer": 1,     # DRAFT: links to this many insolvent companies -> refer
    "director_insolvent_decline": 2,   # DRAFT: this many or more -> outside appetite
    "phoenix_young_months": 24,        # DRAFT: similar-name insolvent predecessor + company younger than this
    "max_directors_checked": 15,
    # Charges
    "charge_burst_count": 3,           # DRAFT: this many new charges ...
    "charge_burst_months": 12,         # ... within this many months -> refer
    # Names
    "rename_recent_months": 60,       # renames in the last 5 years are shown (they explain mismatched news)
}

# Checks that must run for a case to be "note". If any of these couldn't be
# checked, the best a case can be is "refer".
CRITICAL_CHECKS = {"status", "filings", "insolvency", "directors_disqualified",
                   "directors_insolvency", "sanctions"}


def decide(checks: list[CheckResult]) -> tuple[Route, list[str]]:
    """Return the recommended route and the reasons, worst first."""
    route = Route.NOTE
    reasons: list[tuple[int, str]] = []

    for c in checks:
        for f in c.findings:
            if f.route.rank > Route.NOTE.rank:
                reasons.append((f.route.rank, f.text))
            if f.route.rank > route.rank:
                route = f.route
        if c.status == CheckStatus.COULDNT_CHECK and c.critical:
            reasons.append((Route.REFER.rank, f"Couldn't check {c.title.lower()}: {c.not_checked_reason}"))
            if route.rank < Route.REFER.rank:
                route = Route.REFER

    # Combination rule (DRAFT): seriously overdue accounts plus another distress
    # signal is outside appetite even though each alone is a referral.
    rule_ids = {f.rule_id for c in checks for f in c.findings}
    if "accounts_overdue_serious" in rule_ids and rule_ids & {
        "charges_burst", "insolvency_history", "strike_off_proposed"
    }:
        if route.rank < Route.OUTSIDE_APPETITE.rank:
            route = Route.OUTSIDE_APPETITE
        reasons.append((Route.OUTSIDE_APPETITE.rank,
                        "Accounts seriously overdue together with another distress signal (draft combination rule)."))

    ordered = [text for _, text in sorted(reasons, key=lambda r: -r[0])]
    return route, list(dict.fromkeys(ordered))
