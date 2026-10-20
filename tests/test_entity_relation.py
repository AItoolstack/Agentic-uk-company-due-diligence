"""News about a group company is not news about this company (Revolut / Revolut Bank UAB, Monzo / Monzo Bank
Europe DAC, both live on 19 Oct 2026)."""

import asyncio
import json
from datetime import date

import pytest

from src.prescreen.checks import CompanyData
from src.prescreen.llm import JSONChat
from src.prescreen.media import check_media, entity_relation
from src.prescreen.models import CheckStatus, Route


@pytest.mark.parametrize("named,registered,expected", [
    ("Revolut Bank UAB", "REVOLUT LTD", "related"),
    ("Monzo Bank Europe DAC", "MONZO BANK LIMITED", "related"),
    ("Tesco Stores Limited", "TESCO PLC", "related"),
    ("Revolut Ltd's", "REVOLUT LTD", "exact"),
    ("Monzo Bank Ltd", "MONZO BANK LIMITED", "exact"),
    ("Monzo", "MONZO BANK LIMITED", ""),           # brand only: the model's reading stands
    ("Thames Water", "THAMES WATER UTILITIES LIMITED", ""),
    ("", "REVOLUT LTD", ""),
])
def test_entity_relation(named, registered, expected):
    assert entity_relation(named, registered, []) == expected


def test_previous_name_counts_as_exact():
    assert entity_relation("Focus FS Limited", "MONZO BANK LIMITED", ["FOCUS FS LIMITED"]) == "exact"


GROUP_PSC = {"items": [{"kind": "corporate-entity-person-with-significant-control", "name": "PARENT HOLDINGS LTD"}]}


def _run(name, rows, status="active", group=True, titles=None):
    profile = {"company_number": "08804411", "company_name": name, "company_status": status,
               "date_of_creation": "2013-12-06", "has_insolvency_history": False, "links": {}}
    data = CompanyData(number="08804411", profile=profile, today=date(2026, 10, 19),
                       pscs=GROUP_PSC if group else {"items": []})
    results = [{"title": (titles or {}).get(i, f"Story {i}"), "url": f"https://news.example/{i}", "published": "2026-05-01",
                "source": "news.example", "snippet": "fine"} for i in range(len(rows))]
    answer = {"items": [{"i": i, **r} for i, r in enumerate(rows)],
              "trade_consistency": {"verdict": "not_asked", "reason": ""}}
    llm = JSONChat(None, "groq", "test", "", scripted=[json.dumps(answer)])
    return asyncio.run(check_media(data, results, llm, "", "test"))


FINE = {"category": "regulatory_action", "severity": "high", "reason": "AML fine"}


def test_group_company_fine_read_as_yes_is_corrected_to_related_and_never_refers():
    media, _, items = _run("REVOLUT LTD", [{"about_this_company": "yes", "entity_named": "Revolut Bank UAB", **FINE}])
    assert items[0].about_this_company == "related"
    assert "Revolut Bank UAB" in items[0].reason
    assert not [f for f in media.findings if f.route == Route.REFER]
    rel = [f for f in media.findings if f.rule_id == "media_related"]
    assert rel and "(Revolut Bank UAB)" in rel[0].text and "not this one" in rel[0].text
    assert media.status == CheckStatus.INFO  # shown, never "nothing adverse"


def test_model_answer_related_is_kept():
    media, _, items = _run("MONZO BANK LIMITED", [{"about_this_company": "related",
                                                   "entity_named": "Monzo Bank Europe DAC", **FINE}])
    assert items[0].about_this_company == "related"
    assert [f.rule_id for f in media.findings] == ["media_related"]


def test_own_fine_still_refers():
    media, _, items = _run("MONZO BANK LIMITED", [{"about_this_company": "yes", "entity_named": "Monzo Bank Ltd", **FINE}],
                           titles={0: "FCA fines Monzo Bank Ltd £21m for financial crime failings"})
    assert items[0].about_this_company == "yes"
    assert [f.rule_id for f in media.findings] == ["media_high"]
    assert media.status == CheckStatus.FLAG


def test_regulator_action_naming_only_the_brand_is_unsure_not_a_reason():
    # Live re-run, 19 Oct: "Italy fines Revolut over $13 million" named only the brand.
    media, _, items = _run("REVOLUT LTD", [{"about_this_company": "yes", "entity_named": "Revolut", **FINE}])
    assert items[0].about_this_company == "unsure"
    assert "Names only the brand (Revolut)" in items[0].reason
    assert [f.rule_id for f in media.findings] == ["media_unsure"]
    assert not [f for f in media.findings if f.route == Route.REFER]


def test_regulator_action_with_no_entity_named_is_unsure():
    media, _, items = _run("MONZO BANK LIMITED", [{"about_this_company": "yes", **FINE}])
    assert items[0].entity_named == "" and items[0].about_this_company == "unsure"


def test_brand_only_naming_keeps_the_models_answer_for_other_news():
    collapse = {"category": "operational_incident", "severity": "high", "reason": "outage"}
    media, _, items = _run("MONZO BANK LIMITED", [{"about_this_company": "yes", "entity_named": "Monzo", **collapse}])
    assert items[0].about_this_company == "yes"
    assert [f.rule_id for f in media.findings] == ["media_high"]


def test_brand_only_regulator_news_still_counts_for_a_standalone_company():
    # No corporate owner: a small firm's "HSE fines Smith Builders" names the brand, and nothing else shares it.
    media, _, items = _run("REVOLUT LTD", [{"about_this_company": "yes", "entity_named": "Revolut", **FINE}], group=False)
    assert items[0].about_this_company == "yes"
    assert [f.rule_id for f in media.findings] == ["media_high"]


def test_regulator_action_naming_the_entity_with_its_legal_form_counts_in_a_group():
    media, _, items = _run("REVOLUT LTD", [{"about_this_company": "yes", "entity_named": "Revolut Ltd", **FINE}],
                           titles={0: "FCA fines Revolut Ltd over payment controls"})
    assert items[0].about_this_company == "yes"


def test_model_claiming_the_legal_entity_must_be_backed_by_the_text_in_a_group():
    # Live, 20 Oct (rules 20g): the €3.5m Lithuanian fine on Revolut Bank UAB came back as naming "Revolut Ltd".
    media, _, items = _run("REVOLUT LTD", [{"about_this_company": "yes", "entity_named": "Revolut Ltd", **FINE}],
                           titles={0: "Revolut Fined €3.5 Million for AML Failures: What It Means for Digital Banks"})
    assert items[0].about_this_company == "unsure"
    assert "name only the brand" in items[0].reason
    assert not [f for f in media.findings if f.route == Route.REFER]


def test_entity_in_text_treats_limited_and_ltd_alike():
    from src.prescreen.media import entity_in_text
    assert entity_in_text("Monzo Bank Ltd", "FCA fines Monzo Bank Limited £21m")
    assert entity_in_text("Revolut Ltd", "Revolut Ltd's new licence")
    assert not entity_in_text("Revolut Ltd", "Revolut fined €3.5m")
    assert not entity_in_text("", "anything")


def test_related_note_doesnt_repeat_a_bare_brand_as_the_entity():
    media, _, _ = _run("REVOLUT LTD", [{"about_this_company": "related", "entity_named": "Revolut", **FINE}])
    rel = next(f for f in media.findings if f.rule_id == "media_related")
    assert "(Revolut)" not in rel.text and "another company in the group" in rel.text


def test_fine_on_the_auditor_is_not_this_companys_news():
    # Live, 20 Oct (rules 20h): "KPMG ... is fined record £21m" over its Carillion audits counted against Carillion.
    media, _, items = _run("CARILLION PLC", [{"about_this_company": "yes", "entity_named": "KPMG", **FINE}],
                           group=False, titles={0: "KPMG boss says Carillion auditing was very bad as firm is fined record £21m"})
    assert items[0].about_this_company == "no" and "About KPMG" in items[0].reason
    assert not [f for f in media.findings if f.route == Route.REFER]


def test_auditor_fine_in_the_headline_overrides_the_models_entity():
    media, _, items = _run("CARILLION PLC", [{"about_this_company": "yes", "entity_named": "Carillion plc", **FINE}],
                           group=False, titles={0: "KPMG boss says Carillion auditing was 'very bad' as firm is fined record £21m"})
    assert items[0].about_this_company == "no" and items[0].entity_named == "KPMG"


def test_a_person_named_stays_with_the_model_reading():
    media, _, items = _run("CARILLION PLC", [{"about_this_company": "yes", "entity_named": "Richard Howson", **FINE}],
                           group=False)
    assert items[0].about_this_company == "yes"


@pytest.mark.parametrize("title,kept", [
    ("Revolut wins Gibraltar DLT licence via new subsidiary", False),
    ("Revolut receives crypto licence authorisation in Gibraltar", False),
    ("Monzo banking licence granted in Ireland", False),
    ("Regulator revokes Revolut's licence in Gibraltar", True),
])
def test_licence_granted_is_routine(title, kept):
    row = {"about_this_company": "yes", "entity_named": "", "category": "regulatory_action", "severity": "medium",
           "reason": "licence"}
    media, _, items = _run("REVOLUT LTD", [row], group=False, titles={0: title})
    assert (items[0].severity == "medium") is kept
