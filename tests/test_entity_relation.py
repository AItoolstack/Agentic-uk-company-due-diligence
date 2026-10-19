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


def _run(name, rows, status="active"):
    profile = {"company_number": "08804411", "company_name": name, "company_status": status,
               "date_of_creation": "2013-12-06", "has_insolvency_history": False, "links": {}}
    data = CompanyData(number="08804411", profile=profile, today=date(2026, 10, 19))
    results = [{"title": f"Story {i}", "url": f"https://news.example/{i}", "published": "2026-05-01",
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
    assert rel and "Revolut Bank UAB" in rel[0].text and "not this one" in rel[0].text
    assert media.status == CheckStatus.INFO  # shown, never "nothing adverse"


def test_model_answer_related_is_kept():
    media, _, items = _run("MONZO BANK LIMITED", [{"about_this_company": "related",
                                                   "entity_named": "Monzo Bank Europe DAC", **FINE}])
    assert items[0].about_this_company == "related"
    assert [f.rule_id for f in media.findings] == ["media_related"]


def test_own_fine_still_refers():
    media, _, items = _run("MONZO BANK LIMITED", [{"about_this_company": "yes", "entity_named": "Monzo Bank Ltd", **FINE}])
    assert items[0].about_this_company == "yes"
    assert [f.rule_id for f in media.findings] == ["media_high"]
    assert media.status == CheckStatus.FLAG


def test_brand_only_naming_keeps_the_models_answer():
    media, _, items = _run("MONZO BANK LIMITED", [{"about_this_company": "yes", "entity_named": "Monzo", **FINE}])
    assert items[0].about_this_company == "yes"
    assert [f.rule_id for f in media.findings] == ["media_high"]


def test_missing_entity_named_is_harmless():
    media, _, items = _run("MONZO BANK LIMITED", [{"about_this_company": "yes", **FINE}])
    assert items[0].entity_named == "" and items[0].about_this_company == "yes"
