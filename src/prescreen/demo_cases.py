"""
Offline cases for tests and for the demo when USE_MOCK_DATA=true.

Two kinds, and the report says which:
  - REAL: Companies House profile/officer responses captured from the live API
    on 18 Oct 2026 (Wilko, Monzo). Where a supporting response wasn't captured
    (appointments, PSCs, news), it is a hand-written stand-in, marked below.
  - SYNTHETIC: invented companies (numbers start ZZ) built from the underwriter
    personas, so every route and failure mode can be shown without real people.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from src.prescreen.sources.http import SourceError

DATA = Path(__file__).resolve().parents[2] / "data" / "mock_sources" / "prescreen"
TODAY = date(2026, 10, 18)


def _load(name: str) -> dict[str, Any]:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def _d(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _officer(name: str, oid: str, dob: tuple[int, int] | None, appointed: str, role: str = "director",
             resigned: str | None = None) -> dict[str, Any]:
    o: dict[str, Any] = {"name": name, "officer_role": role, "appointed_on": appointed,
                         "links": {"officer": {"appointments": f"/officers/{oid}/appointments"}}}
    if dob:
        o["date_of_birth"] = {"month": dob[0], "year": dob[1]}
    if resigned:
        o["resigned_on"] = resigned
    return o


def _appt(number: str, name: str, status: str, appointed: str, resigned: str | None = None) -> dict[str, Any]:
    a = {"appointed_to": {"company_number": number, "company_name": name, "company_status": status},
         "appointed_on": appointed, "officer_role": "director"}
    if resigned:
        a["resigned_on"] = resigned
    return a


def _profile(number: str, name: str, created: str, *, status: str = "active", detail: str = "",
             acc_due: str = "2027-06-30", acc_overdue: bool = False, cs_due: str = "2027-03-01",
             cs_overdue: bool = False, sic: list[str] | None = None, links_extra: dict | None = None,
             previous: list | None = None, town: str = "Leeds") -> dict[str, Any]:
    p = {
        "company_number": number, "company_name": name, "company_status": status, "date_of_creation": created,
        "type": "ltd", "jurisdiction": "england-wales", "sic_codes": sic or ["10710"],
        "accounts": {"next_accounts": {"due_on": acc_due, "overdue": acc_overdue}},
        "confirmation_statement": {"next_due": cs_due, "overdue": cs_overdue},
        "registered_office_address": {"address_line_1": "1 Example Street", "locality": town, "postal_code": "LS1 1AA"},
        "links": {"self": f"/company/{number}", "officers": f"/company/{number}/officers",
                  "persons_with_significant_control": f"/company/{number}/persons-with-significant-control",
                  **(links_extra or {})},
        "previous_company_names": previous or [],
    }
    if detail:
        p["company_status_detail"] = detail
    return p


def _psc_person(name: str, dob: tuple[int, int]) -> dict[str, Any]:
    return {"kind": "individual-person-with-significant-control", "name": name,
            "date_of_birth": {"month": dob[0], "year": dob[1]},
            "natures_of_control": ["ownership-of-shares-75-to-100-percent"]}


def _psc_corp(name: str, country: str) -> dict[str, Any]:
    return {"kind": "corporate-entity-person-with-significant-control", "name": name,
            "identification": {"country_registered": country, "legal_form": "Limited company"},
            "natures_of_control": ["ownership-of-shares-75-to-100-percent"]}


def _news(title: str, published: str, source: str, snippet: str = "", url: str = "") -> dict[str, str]:
    return {"title": title, "published": published, "source": source, "snippet": snippet,
            "url": url or f"https://{source}/article-{abs(hash(title)) % 10**6}"}


def cases() -> dict[str, dict[str, Any]]:
    """number -> {"label", "kind", "ch": {path: json}, "fca": {...}, "news": [...] | SourceError}"""
    out: dict[str, dict[str, Any]] = {}

    # --- REAL: Wilko Limited (09563205) -----------------------------------
    wn = "09563205"
    out[wn] = {
        "label": "Wilko Limited — real profile; the brand company bought by The Range",
        "kind": "real",
        "ch": {
            f"/company/{wn}": _load("wilko_09563205_profile.json"),
            f"/company/{wn}/officers": _load("wilko_09563205_officers.json"),
            # Stand-in: the 18 Oct run reported the director has 30 appointments, 10 dissolved, none insolvent.
            "/officers/n5o8c10NjR_CDOroiEe_Fol6AdU/appointments": {"items": [
                _appt(wn, "WILKO LIMITED", "active", "2015-04-27")]
                + [_appt(f"0900{i:04d}", f"RANGE GROUP COMPANY {i} LIMITED", "dissolved", "2012-01-01") for i in range(10)]
                + [_appt(f"0800{i:04d}", f"RANGE GROUP COMPANY {i + 10} LIMITED", "active", "2012-01-01") for i in range(19)]},
            f"/company/{wn}/persons-with-significant-control": {"items": [
                {"kind": "corporate-entity-person-with-significant-control", "name": "Superstores International Limited",
                 "natures_of_control": ["ownership-of-shares-75-to-100-percent"]}]},
            "/search/disqualified-officers?q=dawson, christopher frederick": {"items": []},
        },
        "news": [
            _news("Wilko collapses into administration putting 12,000 jobs at risk", "2023-08-10", "bbc.co.uk",
                  "The discount chain has called in administrators after failing to find a buyer."),
            _news("Wilko creditors face vast losses after collapse of family-owned retailer", "2023-09-21",
                  "theguardian.com", "Administrators' report shows unsecured creditors are unlikely to be repaid."),
            _news("The Range buys Wilko brand and website for £5m", "2023-09-15", "expressandstar.com",
                  "The Range has bought the Wilko brand, website and intellectual property."),
            _news("Wilko returns to the high street inside The Range stores", "2025-03-04", "retailgazette.co.uk",
                  "The Range is opening Wilko-branded concessions in its stores."),
        ],
    }

    # --- REAL: Monzo Bank Limited (09446231) -------------------------------
    mn = "09446231"
    out[mn] = {
        "label": "Monzo Bank Limited — real profile; FCA-regulated bank",
        "kind": "real",
        "ch": {
            f"/company/{mn}": _load("monzo_09446231_profile.json"),
            f"/company/{mn}/officers": {"items": [
                _officer("BURBIDGE, Eileen", "ssSVue1XDlQnQSitrtOoyTIe5s0", (6, 1971), "2015-04-21"),
                _officer("DIAS, Valerie Michelle", "EkthAQVfOSXFsB2z9kgZV8WprAs", (10, 1959), "2021-06-01")]},
            "/officers/ssSVue1XDlQnQSitrtOoyTIe5s0/appointments": {"items": [_appt(mn, "MONZO BANK LIMITED", "active", "2015-04-21")]},
            "/officers/EkthAQVfOSXFsB2z9kgZV8WprAs/appointments": {"items": [_appt(mn, "MONZO BANK LIMITED", "active", "2021-06-01")]},
            f"/company/{mn}/persons-with-significant-control": {"items": [
                _psc_corp("Monzo Bank Holding Group Limited", "England And Wales")]},
            f"/company/{mn}/persons-with-significant-control-statements": {"items": []},
            f"/company/{mn}/charges": {"items": [], "total_count": 0},
        },
        "fca": {
            "/search?q=monzo bank limited": {"Data": [
                {"Name": "Monzo Bank Limited (Postcode: EC2A 2AG)", "Reference Number": "730427",
                 "Status": "Authorised", "Type of business or Individual": "Firm"}]},
            "/Firm/730427/DisciplinaryHistory": {"Data": [
                {"TypeofAction": "Final Notice", "EnforcementType": "Financial penalty (£21.1m)",
                 "ActionEffectiveFrom": "08/07/2025", "TypeofDescription": "Failings in financial crime controls"}]},
        },
        "news": [
            _news("FCA fines Monzo £21m for failings in financial crime controls", "2025-07-08", "fca.org.uk",
                  "The FCA has fined Monzo Bank Ltd £21,091,300 for failings in its financial crime controls."),
            _news("Monzo reports first full-year profit", "2024-06-03", "bbc.co.uk", "The digital bank made a profit."),
        ],
    }

    # --- SYNTHETIC: phoenix pattern (persona: Priya) -----------------------
    zn = "ZZ000001"
    out[zn] = {
        "label": "Northern Bake Ltd — SYNTHETIC: 7 months old, director's similar-named company in liquidation",
        "kind": "synthetic",
        "ch": {
            f"/company/{zn}": _profile(zn, "NORTHERN BAKE LTD", _d(213)),
            f"/company/{zn}/officers": {"items": [_officer("EXAMPLE, Jordan Alex", "zzofficer1", (3, 1980), _d(213))]},
            "/officers/zzofficer1/appointments": {"items": [
                _appt(zn, "NORTHERN BAKE LTD", "active", _d(213)),
                _appt("ZZ900001", "NORTHERN BAKES LIMITED", "liquidation", "2016-05-01", _d(240)),
                _appt("ZZ900002", "EXAMPLE CATERING LTD", "liquidation", "2019-02-01", "2023-01-01"),
                _appt("ZZ900003", "EXAMPLE EVENTS LTD", "dissolved", "2017-04-01", "2022-06-30"),
                _appt("ZZ900004", "EXAMPLE PROPERTY LTD", "dissolved", "2015-01-01", "2020-01-01")]},
            # A company that went through liquidation and is now dissolved still counts.
            "/company/ZZ900003": {"company_number": "ZZ900003", "company_name": "EXAMPLE EVENTS LTD",
                                  "company_status": "dissolved", "date_of_cessation": "2023-09-01",
                                  "links": {"insolvency": "/company/ZZ900003/insolvency"}},
            # Dissolved without insolvency: housekeeping, not counted.
            "/company/ZZ900004": {"company_number": "ZZ900004", "company_name": "EXAMPLE PROPERTY LTD",
                                  "company_status": "dissolved", "date_of_cessation": "2021-03-01", "links": {}},
            f"/company/{zn}/persons-with-significant-control": {"items": [_psc_person("Jordan Alex EXAMPLE", (3, 1980))]},
        },
        "news": [],
    }

    # --- SYNTHETIC: distress (overdue + strike-off + charges) --------------
    zd = "ZZ000002"
    out[zd] = {
        "label": "Halden Joinery Ltd — SYNTHETIC: accounts 140 days overdue, strike-off proposed, new charges",
        "kind": "synthetic",
        "ch": {
            f"/company/{zd}": _profile(zd, "HALDEN JOINERY LTD", "2011-03-14", detail="active-proposal-to-strike-off",
                                       acc_due=_d(140), acc_overdue=True, cs_due=_d(60), cs_overdue=True,
                                       sic=["16230"], links_extra={"charges": f"/company/{zd}/charges"}),
            f"/company/{zd}/officers": {"items": [_officer("SAMPLE, Morgan", "zzofficer2", (8, 1971), "2011-03-14")]},
            "/officers/zzofficer2/appointments": {"items": [_appt(zd, "HALDEN JOINERY LTD", "active", "2011-03-14")]},
            f"/company/{zd}/persons-with-significant-control": {"items": [_psc_person("Morgan SAMPLE", (8, 1971))]},
            f"/company/{zd}/charges": {"items": [
                {"status": "outstanding", "created_on": _d(d), "persons_entitled": [{"name": n}]}
                for d, n in ((30, "Quickcash Finance Ltd"), (90, "Bridge Lending Ltd"), (200, "Asset Funder Ltd"),
                             (2000, "Example Bank plc"))]},
            f"/company/{zd}/filing-history": {"items": [
                {"type": "GAZ1", "date": _d(20), "description": "gazette-notice-compulsory"}]},
        },
        "gazette": [{"code": "2450", "title": "Petitions to Wind Up (Companies)", "published": _d(25),
                     "url": "https://www.thegazette.co.uk/notice/EXAMPLE-0001", "snippet": "HALDEN JOINERY LTD"}],
        "news": [],
    }

    # --- SYNTHETIC: sources down --------------------------------------------
    zs = "ZZ000003"
    out[zs] = {
        "label": "Calder Valley Plumbing Ltd — SYNTHETIC: Companies House partly down, search down",
        "kind": "synthetic",
        "ch": {
            f"/company/{zs}": _profile(zs, "CALDER VALLEY PLUMBING LTD", "2014-09-01", sic=["43220"]),
            f"/company/{zs}/officers": SourceError("Companies House", "unavailable", "HTTP 503"),
            f"/company/{zs}/persons-with-significant-control": SourceError("Companies House", "unavailable", "ReadTimeout"),
        },
        "news": SourceError("Brave Search", "rate_limited", "HTTP 429"),
    }

    # --- SYNTHETIC: sanctions hit on a PSC ----------------------------------
    za = "ZZ000004"
    out[za] = {
        "label": "Atlas Trade Partners Ltd — SYNTHETIC: BVI owner; a PSC matches a (fictional) sanctions entry",
        "kind": "synthetic",
        "ch": {
            f"/company/{za}": _profile(za, "ATLAS TRADE PARTNERS LTD", "2019-06-01", sic=["46900"]),
            f"/company/{za}/officers": {"items": [_officer("SAMPLE, Robin", "zzofficer4", (1, 1975), "2019-06-01")]},
            "/officers/zzofficer4/appointments": {"items": [_appt(za, "ATLAS TRADE PARTNERS LTD", "active", "2019-06-01")]},
            f"/company/{za}/persons-with-significant-control": {"items": [
                _psc_corp("Meridian Holdings SA", "British Virgin Islands"),
                _psc_person("Viktor Ivanovich FICTIONOV", (4, 1966))]},
        },
        "news": [],
    }
    # --- SYNTHETIC: LLP with members (PI cover) --------------------------
    zl = "OC000005"
    out[zl] = {
        "label": "Pennine Advisory LLP — SYNTHETIC: LLP members, a member resigned recently, PI cover",
        "kind": "synthetic",
        "ch": {
            f"/company/{zl}": {**_profile(zl, "PENNINE ADVISORY LLP", "2009-05-01", sic=["69201"]), "type": "llp"},
            f"/company/{zl}/officers": {"items": [
                _officer("PATEL, Asha", "zzllp1", (2, 1972), "2009-05-01", role="llp-designated-member"),
                _officer("HUGHES, Owen", "zzllp2", (9, 1968), "2009-05-01", role="llp-designated-member"),
                _officer("BROOK, Sam", "zzllp3", (5, 1985), "2018-01-01", role="llp-member", resigned=_d(60)),
            ]},
            "/officers/zzllp1/appointments": {"items": [_appt(zl, "PENNINE ADVISORY LLP", "active", "2009-05-01")]},
            "/officers/zzllp2/appointments": {"items": [_appt(zl, "PENNINE ADVISORY LLP", "active", "2009-05-01")]},
            "/officers/zzllp3/appointments": {"items": [_appt(zl, "PENNINE ADVISORY LLP", "active", "2018-01-01", _d(60))]},
            f"/company/{zl}/persons-with-significant-control": {"items": [
                {**_psc_person("Asha PATEL", (2, 1972)), "notified_on": "2016-06-30"}]},
        },
        "news": [],
    }
    return out


FICTIONAL_SANCTIONS_CSV = """Report Date: 18-Oct-2026
Last Updated,Unique ID,OFSI Group ID,Name 6,Name 1,Name 2,Name 3,Name 4,Name 5,Name type,"Individual, Entity, Ship",D.O.B,Regime Name
18/10/2026,FIC0001,90001,FICTIONOV,Viktor,Ivanovich,,,,Primary Name,Individual,12/04/1966,Fictional Regime (demo data)
18/10/2026,FIC0002,90002,EXAMPLE SHIPPING COMPANY,,,,,,Primary Name,Entity,,Fictional Regime (demo data)
18/10/2026,FIC0003,90003,EXAMPLE,Jordan,,,,,Primary Name,Individual,dd/mm/1955,Fictional Regime (demo data)
"""
