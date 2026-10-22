"""Shared test helpers (no fixtures here; see conftest.py)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

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


# --------------------------------------------------------------------------- scripted FunctionModel for tests


Step = str | list[tuple[str, dict[str, Any]]] | Callable[[list[ModelMessage], AgentInfo], ModelResponse]


def last_tool_text(messages: list[ModelMessage]) -> str:
    """Content of the most recent tool return (skipping retry prompts)."""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            returns = [p for p in message.parts if isinstance(p, ToolReturnPart)]
            if returns:
                return str(returns[-1].content)
    return ""


def final(
    message: str, intent: str = "conversa", **extra: Any
) -> Callable[[list[ModelMessage], AgentInfo], ModelResponse]:
    def step(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"message": message, "intent": intent, **extra})]
        )

    return step


def echo_summary(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Final reply that repeats the «summary» from the last tool return."""
    text = last_tool_text(messages)
    summary = text.split("«", 1)[1].split("»", 1)[0] if "«" in text else text
    return final(f"Confirma? {summary}", "proposta")(messages, info)


def echo_after(marker: str) -> Callable[[list[ModelMessage], AgentInfo], ModelResponse]:
    def step(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        text = last_tool_text(messages)
        return final(text.split(marker, 1)[-1].strip(), "confirmado")(messages, info)

    return step


class ScriptModel:
    """FunctionModel driven by a queue of steps (one per model request)."""

    def __init__(self, *steps: Step) -> None:
        self.steps: list[Step] = list(steps)
        self.calls = 0
        self.seen: list[list[ModelMessage]] = []
        self.model = FunctionModel(self._fn, model_name="script")

    def add(self, *steps: Step) -> None:
        self.steps.extend(steps)

    def _fn(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.calls += 1
        self.seen.append(messages)
        if not self.steps:
            raise AssertionError("ScriptModel ran out of steps")
        step = self.steps.pop(0)
        if isinstance(step, str):
            return final(step)(messages, info)
        if callable(step):
            return step(messages, info)
        return ModelResponse(parts=[ToolCallPart(name, args) for name, args in step])
