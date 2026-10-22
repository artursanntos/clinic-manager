from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.models import mask_phone, normalize_phone
from app.domain.rules import ClinicRules, Weekday, load_rules
from tests.helpers import make_rules, rules_dict

ROOT = Path(__file__).resolve().parents[2]


def test_example_config_loads() -> None:
    rules = load_rules(ROOT / "config" / "clinic.yaml")
    assert rules.timezone == "America/Recife"
    assert rules.procedure("avaliacao") is not None
    assert rules.working_hours[Weekday.MONDAY]


def test_capability_config(rules: ClinicRules) -> None:
    assert rules.capability_config("waitlist") == {"max_offers_per_slot": 2}
    assert rules.capability_config("unknown") is None


def test_disabled_capability() -> None:
    rules = make_rules(capabilities={"recall": {"enabled": False, "after_months": 6}})
    assert rules.capability_config("recall") is None


def test_invalid_time_range() -> None:
    data = rules_dict(working_hours={"monday": [{"start": "12:00", "end": "08:00"}]})
    with pytest.raises(ValidationError):
        ClinicRules.model_validate(data)


def test_duplicate_procedure_ids() -> None:
    data = rules_dict()
    data["procedures"] = data["procedures"] + [data["procedures"][0]]
    with pytest.raises(ValidationError):
        ClinicRules.model_validate(data)


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        ClinicRules.model_validate(rules_dict(typo_field=1))


def test_invalid_timezone() -> None:
    with pytest.raises(ValidationError):
        ClinicRules.model_validate(rules_dict(timezone="Mars/Olympus"))


def test_holiday_lookup(rules: ClinicRules) -> None:
    assert rules.holiday_on(date(2026, 11, 2)) is not None
    assert rules.holiday_on(date(2026, 11, 3)) is None


def test_phone_helpers() -> None:
    assert normalize_phone("+55 (81) 99999-0000") == "5581999990000"
    assert mask_phone("5581999990000") == "5581*****0000"
    with pytest.raises(ValueError):
        normalize_phone("123")


def test_brazilian_phone_variants() -> None:
    from app.domain.models import phone_variants, same_phone

    assert phone_variants("5581988887777") == ["5581988887777", "558188887777"]
    assert phone_variants("558188887777") == ["558188887777", "5581988887777"]
    assert same_phone("558188887777", "5581988887777")
    assert not same_phone("5581988887777", "5581988887778")
    assert phone_variants("14155550100") == ["14155550100"]
