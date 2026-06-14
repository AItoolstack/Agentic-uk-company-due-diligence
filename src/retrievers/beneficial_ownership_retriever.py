"""
retrievers/beneficial_ownership_retriever.py
--
BeneficialOwnershipRetriever -- maps the BENEFICIAL_OWNERSHIP dimension.

Responsibility:
  1. Fetch the PSC register via CompaniesHousePSCConnector.
  2. Fetch PSC control statements (opacity signal).
  3. Flag offshore / high-risk corporate PSCs by jurisdiction.
  4. Detect concentration of control (75-100% thresholds).
  5. Flag super-secure PSCs (name redacted by court order -- unusual signal).
  6. Flag opacity patterns: no named individuals, control statements filed.
  7. Screen named individual PSCs through OpenSanctions (PEP / sanctions).
  8. Assemble all PSC risk flags into a single EvidenceItem.

Insurance underwriting relevance:
  - D&O / FI: offshore ultimate owner increases governance risk.
  - Trade Credit: concentrated control by a single unknown entity raises credit risk.
  - Cyber: corporate PSCs in high-risk jurisdictions sometimes correlate with
    higher fraud / data exfiltration incidents.
  - AML: any high-risk jurisdiction or opacity flag triggers enhanced due diligence.

Design:
  Zero LLM calls -- pure connector -> EvidenceItem.
  OpenSanctions screening is only run for natural-person PSCs to avoid noise.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.connectors.base import ConnectorError
from src.connectors.companies_house_psc import (
    CompaniesHousePSCConnector,
    HIGH_RISK_JURISDICTIONS,
    CROWN_DEPENDENCY_JURISDICTIONS,
    OPACITY_STATEMENT_KINDS,
    get_jurisdiction,
)
from src.connectors.open_sanctions import OpenSanctionsConnector
from src.retrievers.base import BaseRetriever
from src.schemas import EvidenceItem, EvidenceQuality, ResearchDimension

# natures_of_control strings that indicate majority / dominant ownership
_DOMINANT_CONTROL_INDICATORS: frozenset[str] = frozenset({
    "ownership-of-shares-75-to-100-percent",
    "voting-rights-75-to-100-percent",
    "right-to-appoint-and-remove-directors",
    "significant-influence-or-control",
})


class BeneficialOwnershipRetriever(BaseRetriever):
    dimension = ResearchDimension.BENEFICIAL_OWNERSHIP

    def __init__(self) -> None:
        psc_connector = CompaniesHousePSCConnector()
        super().__init__(psc_connector)
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
            return self._error_evidence(ConnectorError(f"BeneficialOwnershipRetriever error: {e}"))

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    def _collect(self, company_number: str, company_name: str) -> EvidenceItem:
        conn = self.connector  # CompaniesHousePSCConnector

        # 1. Fetch PSC register
        psc_data = conn.fetch_pscs(company_number)  # type: ignore[attr-defined]
        statements = conn.fetch_psc_statements(company_number)  # type: ignore[attr-defined]

        by_kind = psc_data.get("items_by_kind", {})
        natural_persons: list[dict[str, Any]] = by_kind.get("natural_persons", [])
        corporates: list[dict[str, Any]] = by_kind.get("corporates", [])
        legal_entities: list[dict[str, Any]] = by_kind.get("legal_entities", [])
        super_secure: list[dict[str, Any]] = by_kind.get("super_secure", [])
        all_pscs = psc_data.get("items", [])

        # 2. Compute risk flags
        risk_flags: list[str] = []

        # 2a. Offshore / high-risk jurisdiction
        offshore_flags = _check_offshore(corporates + legal_entities)
        risk_flags.extend(offshore_flags)

        # 2b. Crown dependency flags (lower severity, still noted)
        crown_flags = _check_crown_dependencies(corporates + legal_entities)
        risk_flags.extend(crown_flags)

        # 2c. Concentration of control
        concentration_flags = _check_concentration(all_pscs)
        risk_flags.extend(concentration_flags)

        # 2d. Super-secure PSC (name redacted by court order)
        if super_secure:
            risk_flags.append(
                f"SUPER-SECURE PSC: {len(super_secure)} PSC(s) with name suppressed by court order -- unusual, requires enhanced due diligence"
            )

        # 2e. Opacity: no named individuals when corporate PSCs exist
        if (corporates or legal_entities) and not natural_persons:
            risk_flags.append(
                "No named individuals as PSCs -- beneficial ownership may be opaque (typical for institutional ownership; flag if structure is complex)"
            )

        # 2f. PSC statements filed (opacity / exemption signals)
        opacity_statements = [
            s for s in statements
            if s.get("statement", "") in OPACITY_STATEMENT_KINDS
        ]
        for stmt in opacity_statements:
            risk_flags.append(
                f"PSC STATEMENT filed: {stmt.get('statement', 'unknown')} (notified {stmt.get('notified_on', 'unknown')})"
            )

        # 3. Screen natural-person PSCs via OpenSanctions
        individual_names = [p.get("name", "") for p in natural_persons if p.get("name")]
        sanctions_hits: list[str] = []
        screened_count = 0
        if individual_names:
            result = self._sanctions.screen_entities(
                company_name="",
                officer_names=individual_names,
            )
            screened_count = result.get("screened_count", 0)
            for key, matches in result.get("hits", {}).items():
                for match in matches:
                    caption = match.get("caption", "unknown")
                    datasets = ", ".join(match.get("datasets", [])[:3])
                    score = match.get("score", 0.0)
                    hit = f"{caption} | lists: {datasets} | score: {score:.2f}"
                    risk_flags.append(f"SANCTIONS/PEP HIT on PSC {key}: {hit}")
                    sanctions_hits.append(hit)

        # 4. Assemble EvidenceItem
        return self._build_evidence_item(
            psc_data=psc_data,
            natural_persons=natural_persons,
            corporates=corporates,
            legal_entities=legal_entities,
            super_secure=super_secure,
            statements=statements,
            risk_flags=risk_flags,
            sanctions_hits=sanctions_hits,
            screened_count=screened_count,
        )

    def _build_evidence_item(
        self,
        psc_data: dict[str, Any],
        natural_persons: list[dict[str, Any]],
        corporates: list[dict[str, Any]],
        legal_entities: list[dict[str, Any]],
        super_secure: list[dict[str, Any]],
        statements: list[dict[str, Any]],
        risk_flags: list[str],
        sanctions_hits: list[str],
        screened_count: int,
    ) -> EvidenceItem:
        total_pscs = psc_data.get("active_count", len(psc_data.get("items", [])))

        # Quality / confidence based on flags found
        critical_flags = [f for f in risk_flags if any(kw in f for kw in ("SANCTIONS", "SUPER-SECURE", "offshore", "HIGH-RISK"))]
        if critical_flags:
            quality = EvidenceQuality.HIGH
            confidence = 0.95
        elif risk_flags:
            quality = EvidenceQuality.MEDIUM
            confidence = 0.80
        else:
            quality = EvidenceQuality.HIGH
            confidence = 0.90

        # Summary
        parts: list[str] = [
            f"Total PSCs: {total_pscs}",
            f"Natural persons: {len(natural_persons)}",
            f"Corporate PSCs: {len(corporates)}",
            f"Legal entities: {len(legal_entities)}",
            f"Statements filed: {len(statements)}",
            f"Risk flags: {len(risk_flags)}",
        ]
        if risk_flags:
            # Include first 3 flags in summary
            parts.append("FLAGS: " + "; ".join(risk_flags[:3]))
        if not risk_flags:
            parts.append("No adverse PSC signals detected")

        summary = " | ".join(parts)

        raw_data: dict[str, Any] = {
            "active_count": total_pscs,
            "natural_persons": [
                {"name": p.get("name"), "natures_of_control": p.get("natures_of_control", [])}
                for p in natural_persons
            ],
            "corporates": [
                {
                    "name": c.get("name"),
                    "jurisdiction": get_jurisdiction(c),
                    "natures_of_control": c.get("natures_of_control", []),
                }
                for c in corporates
            ],
            "statements_filed": [s.get("statement") for s in statements],
            "psc_risk_flags": risk_flags,
            "sanctions_hits": sanctions_hits,
            "screened_individuals": screened_count,
        }

        return EvidenceItem(
            source="Companies House PSC Register + OpenSanctions",
            dimension=self.dimension,
            retrieved_at=datetime.utcnow(),
            raw_data=raw_data,
            summary=summary,
            quality=quality,
            confidence=confidence,
        )


# ------------------------------------------------------------------
# Pure analysis helpers
# ------------------------------------------------------------------

def _check_offshore(entities: list[dict[str, Any]]) -> list[str]:
    """Flag corporate / legal PSCs registered in high-risk jurisdictions."""
    flags: list[str] = []
    for entity in entities:
        jurisdiction = get_jurisdiction(entity)
        name = entity.get("name", "Unknown entity")
        if jurisdiction in HIGH_RISK_JURISDICTIONS:
            flags.append(
                f"HIGH-RISK OFFSHORE PSC: {name} registered in {jurisdiction.title()} -- AML / governance risk"
            )
    return flags


def _check_crown_dependencies(entities: list[dict[str, Any]]) -> list[str]:
    """Flag corporate / legal PSCs in UK Crown Dependencies."""
    flags: list[str] = []
    for entity in entities:
        jurisdiction = get_jurisdiction(entity)
        name = entity.get("name", "Unknown entity")
        if jurisdiction in CROWN_DEPENDENCY_JURISDICTIONS:
            flags.append(
                f"Crown dependency PSC: {name} registered in {jurisdiction.title()} -- note for enhanced due diligence"
            )
    return flags


def _check_concentration(pscs: list[dict[str, Any]]) -> list[str]:
    """Flag PSCs with dominant / majority control indicators."""
    flags: list[str] = []
    for psc in pscs:
        name = psc.get("name", "Unknown PSC")
        controls = psc.get("natures_of_control", [])
        dominant = [c for c in controls if c in _DOMINANT_CONTROL_INDICATORS]
        if dominant:
            readable = ", ".join(c.replace("-", " ") for c in dominant)
            flags.append(
                f"Concentrated control: {name} holds [{readable}]"
            )
    return flags
