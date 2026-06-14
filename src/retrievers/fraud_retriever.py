"""
retrievers/fraud_retriever.py
--
FraudRetriever -- orchestrates the FRAUD_SIGNALS research dimension.

Responsibility:
  1. Fetch company officers with officer IDs (CompaniesHouseFraudConnector).
  2. Check each officer against the disqualification register.
  3. Fetch officer appointment history and compute phoenix risk score.
  4. Screen company + officer names via OpenSanctions.
  5. Assemble all signals into a single EvidenceItem.

Phoenix pattern detection:
  A "phoenix company" pattern occurs when a director repeatedly runs companies
  into insolvency and starts new ones, leaving creditors unpaid.
  Score = dissolved_ratio * recency_weight (0.0-1.0).
  Score thresholds: >0.3 = medium risk, >0.5 = high risk, >0.7 = critical.

Design:
  Zero LLM calls -- this is pure connector -> EvidenceItem.
  Disqualification and appointment checks are run for active directors only
  (resigned officers are included in appointment history but not prioritised).
  Capped at 5 officers to stay within API rate limits for the POC.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Any

from src.connectors.base import ConnectorError
from src.connectors.companies_house_fraud import (
    CompaniesHouseFraudConnector,
    FAILED_STATUSES,
)
from src.connectors.open_sanctions import OpenSanctionsConnector
from src.retrievers.base import BaseRetriever
from src.schemas import EvidenceItem, EvidenceQuality, ResearchDimension, RiskLevel

# Phoenix risk score thresholds
_PHOENIX_MEDIUM = 0.30
_PHOENIX_HIGH   = 0.50
_PHOENIX_CRITICAL = 0.70

# Officers checked per run (keep low to respect CH rate limits)
_MAX_OFFICERS = 5


class FraudRetriever(BaseRetriever):
    dimension = ResearchDimension.FRAUD_SIGNALS

    def __init__(self) -> None:
        ch_fraud = CompaniesHouseFraudConnector()
        super().__init__(ch_fraud)
        self._sanctions = OpenSanctionsConnector()

    def retrieve(
        self,
        company_number: str,
        company_name: str = "",
        **kwargs: object,
    ) -> EvidenceItem:
        try:
            return self._collect(company_number, company_name)
        except ConnectorError as e:
            return self._error_evidence(e)
        except Exception as e:
            return self._error_evidence(ConnectorError(f"FraudRetriever error: {e}"))

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    def _collect(self, company_number: str, company_name: str) -> EvidenceItem:
        ch = self.connector  # CompaniesHouseFraudConnector

        # 1. Fetch officers with their CH IDs
        officers = ch.fetch_officers_with_ids(company_number)  # type: ignore[attr-defined]
        active_officers = [o for o in officers if not o.get("resigned_on")][:_MAX_OFFICERS]
        all_officers = officers[:_MAX_OFFICERS]

        officer_names = [o["name"] for o in active_officers]

        # 2. Disqualification checks
        disqualified: list[str] = []
        for officer in all_officers:
            oid = officer.get("officer_id")
            name = officer["name"]
            if oid:
                record = ch.check_disqualification_by_id(oid)  # type: ignore[attr-defined]
                if record:
                    disqualified.append(name)
            else:
                # Fallback: name-based search
                matches = ch.search_disqualified_by_name(name)  # type: ignore[attr-defined]
                if matches:
                    disqualified.append(name)

        # 3. Appointment history -- phoenix pattern
        phoenix_score, phoenix_evidence = self._compute_phoenix(
            ch, all_officers
        )

        # 4. OpenSanctions screening
        sanctions_result = self._sanctions.screen_entities(
            company_name=company_name,
            officer_names=officer_names,
        )
        sanctions_hits = self._extract_sanctions_hits(sanctions_result)

        # 5. Assemble evidence
        return self._build_evidence_item(
            company_name=company_name,
            officers_checked=len(all_officers),
            disqualified=disqualified,
            phoenix_score=phoenix_score,
            phoenix_evidence=phoenix_evidence,
            sanctions_result=sanctions_result,
            sanctions_hits=sanctions_hits,
        )

    def _compute_phoenix(
        self,
        ch: CompaniesHouseFraudConnector,
        officers: list[dict[str, Any]],
    ) -> tuple[float, list[str]]:
        """Compute aggregate phoenix risk score across all officers."""
        all_evidence: list[str] = []
        max_score = 0.0

        for officer in officers:
            oid = officer.get("officer_id")
            if not oid:
                continue

            appointments = ch.fetch_appointment_history(oid)  # type: ignore[attr-defined]
            if not appointments:
                continue

            score, evidence = _phoenix_score(officer["name"], appointments)
            if score > max_score:
                max_score = score
            all_evidence.extend(evidence)

        return max_score, all_evidence

    @staticmethod
    def _extract_sanctions_hits(result: dict[str, Any]) -> list[str]:
        """Convert OpenSanctions response into readable hit strings."""
        hits = result.get("hits", {})
        output: list[str] = []
        for entity_key, matches in hits.items():
            for match in matches:
                caption = match.get("caption", "unknown")
                datasets = ", ".join(match.get("datasets", [])[:3])
                score = match.get("score", 0.0)
                output.append(
                    f"{caption} | lists: {datasets} | score: {score:.2f} | query: {entity_key}"
                )
        return output

    def _build_evidence_item(
        self,
        company_name: str,
        officers_checked: int,
        disqualified: list[str],
        phoenix_score: float,
        phoenix_evidence: list[str],
        sanctions_result: dict[str, Any],
        sanctions_hits: list[str],
    ) -> EvidenceItem:
        # Determine overall quality and risk level
        has_critical = bool(sanctions_hits) or bool(disqualified)
        has_high = phoenix_score >= _PHOENIX_HIGH

        if has_critical:
            quality = EvidenceQuality.HIGH  # high quality signal, not low evidence
            confidence = 0.95
        elif has_high:
            quality = EvidenceQuality.HIGH
            confidence = 0.90
        elif phoenix_score >= _PHOENIX_MEDIUM:
            quality = EvidenceQuality.MEDIUM
            confidence = 0.80
        else:
            quality = EvidenceQuality.HIGH
            confidence = 0.90

        # Build summary
        parts: list[str] = [
            f"Officers checked: {officers_checked}",
            f"Disqualified officers: {len(disqualified)}",
            f"Phoenix risk score: {phoenix_score:.2f}",
            f"Sanctions hits: {len(sanctions_hits)}",
            f"Entities screened: {sanctions_result.get('screened_count', 0)}",
        ]
        if disqualified:
            parts.append(f"DISQUALIFIED: {', '.join(disqualified)}")
        if sanctions_hits:
            parts.append(f"SANCTIONS HITS: {'; '.join(sanctions_hits[:2])}")
        if phoenix_evidence:
            parts.append(f"Phoenix evidence: {'; '.join(phoenix_evidence[:2])}")

        summary = " | ".join(parts)

        raw_data: dict[str, Any] = {
            "company_name": company_name,
            "officers_checked": officers_checked,
            "disqualified_officers": disqualified,
            "phoenix_risk_score": phoenix_score,
            "phoenix_evidence": phoenix_evidence,
            "sanctions_hits": sanctions_hits,
            "sanctions_screened_count": sanctions_result.get("screened_count", 0),
        }

        return EvidenceItem(
            source="Companies House (Disqualification + Appointments) + OpenSanctions",
            dimension=self.dimension,
            retrieved_at=datetime.utcnow(),
            raw_data=raw_data,
            summary=summary,
            quality=quality,
            confidence=confidence,
        )


# ------------------------------------------------------------------
# Phoenix scoring algorithm (module-level, pure function)
# ------------------------------------------------------------------

def _phoenix_score(
    officer_name: str,
    appointments: list[dict[str, Any]],
) -> tuple[float, list[str]]:
    """Calculate phoenix risk score for a single officer.

    Score = dissolved_ratio * recency_weight
    Recency weight: 1.5x if any failure within last 5 years, else 1.0x
    Capped at 1.0.

    Returns (score, evidence_strings).
    """
    if not appointments:
        return 0.0, []

    total = len(appointments)
    failed = [
        a for a in appointments
        if a.get("company_status", "").lower() in FAILED_STATUSES
    ]

    if not failed:
        return 0.0, []

    dissolved_ratio = len(failed) / total

    # Recency: check if any failure in the last 5 years
    current_year = date.today().year
    recency_weight = 1.0
    for appt in failed:
        appointed = appt.get("appointed_on", "")
        if appointed:
            try:
                year = int(appointed[:4])
                if current_year - year <= 5:
                    recency_weight = 1.5
                    break
            except (ValueError, TypeError):
                pass

    score = min(dissolved_ratio * recency_weight, 1.0)

    evidence: list[str] = [
        f"{officer_name}: {len(failed)}/{total} companies dissolved/failed"
    ]
    for a in failed[:3]:
        co_name = a.get("company_name", "Unknown")
        co_number = a.get("company_number", "")
        status = a.get("company_status", "unknown")
        evidence.append(f"  -> {co_name} ({co_number}) status: {status}")

    return score, evidence
