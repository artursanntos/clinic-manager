"""Shared test helpers (no fixtures here; see conftest.py)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.domain.models import BusyInterval
from app.domain.rules import ClinicRules

TZ = ZoneInfo("America/Recife")

# 2026-10-26 is a Monday.
MONDAY = (2026, 10, 26)


def at(y: int, m: int, d: int, hh: int = 0, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def mon(hh: int, mm: int = 0) -> datetime:
    return at(*MONDAY, hh, mm)


def busy(start: datetime, end: datetime, event_id: str | None = None) -> BusyInterval:
    return BusyInterval(start=start, end=end, event_id=event_id)


def rules_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "clinic": {
            "name": "Clínica Teste",
            "dentist_name": "Dra. Teste",
            "dentist_phone": "5581900000001",
            "address": "Rua Teste, 1",
            "extra_info": {"estacionamento": "Há vagas na rua."},
        },
        "timezone": "America/Recife",
        "scheduling": {
            "slot_step_minutes": 30,
            "buffer_minutes": 0,
            "min_lead_minutes": 120,
            "max_lead_days": 30,
            "max_options": 6,
            "max_options_per_day": 3,
            "fill_gaps": True,
        },
        "working_hours": {
            day: [{"start": "08:00", "end": "12:00"}, {"start": "13:30", "end": "18:00"}]
            for day in ("monday", "tuesday", "wednesday", "thursday")
        }
        | {
            "friday": [{"start": "08:00", "end": "12:00"}, {"start": "13:30", "end": "17:00"}],
            "saturday": [{"start": "08:00", "end": "12:00"}],
        },
        "encaixe_windows": {"monday": [{"start": "12:00", "end": "12:30"}]},
        "holidays": [{"date": "2026-11-02", "name": "Finados"}],
        "procedures": [
            {
                "id": "avaliacao",
                "name": "Avaliação",
                "duration_minutes": 30,
                "price_cents": 0,
                "price_note": "Gratuita",
                "aliases": ["consulta", "checkup"],
            },
            {
                "id": "limpeza",
                "name": "Limpeza",
                "duration_minutes": 60,
                "price_cents": 18000,
                "aliases": ["profilaxia"],
            },
            {
                "id": "canal",
                "name": "Canal",
                "duration_minutes": 90,
                "price_note": "sob avaliação",
                "bookable_by_bot": False,
            },
        ],
        "insurance": {"accepted": ["Odonto Teste"]},
        "cancellation_policy": {"min_notice_hours": 24, "text": "Cancele com 24h de antecedência."},
        "approvals": {"encaixe": True, "late_cancellation": False, "new_patient": False},
        "emergency_guidance": "Procure um pronto-socorro ou ligue 192.",
        "capabilities": {
            "waitlist": {"enabled": True, "max_offers_per_slot": 2},
            "recall": {"enabled": True, "after_months": 6, "procedure_ids": ["limpeza"]},
            "followup": {"enabled": True, "review_url": "https://example.test/review"},
        },
    }
    base.update(overrides)
    return base


def make_rules(**overrides: Any) -> ClinicRules:
    return ClinicRules.model_validate(rules_dict(**overrides))
