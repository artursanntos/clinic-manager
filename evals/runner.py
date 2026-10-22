"""Scenario evals: replay conversations through the REAL harness with any model and check the outcomes.

``uv run clinic eval --model test`` runs in CI (scripted model, no network).
``uv run clinic eval --model openrouter:<model>`` measures a real model (needs its API key). Use only fake data.

Besides each scenario's expectations, every turn is checked for universal invariants:
- a write (new/changed/cancelled appointment) only happens on a turn that confirmed a proposal from a previous turn;
- every price (R$) in a reply exists in clinic.yaml;
- every time (HH:MM) in a reply was offered by the code, is an existing appointment, or was written by the patient.
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.adapters.calendar.fake import FakeCalendar
from app.adapters.messaging.base import InboundMessage
from app.adapters.messaging.fake import FakeMessaging
from app.adapters.storage.memory import InMemoryStorage
from app.agent.model import resolve_model
from app.agent.validators import prices_in, times_in
from app.config import Settings
from app.container import Container, build_container
from app.domain.models import Appointment, ConversationState
from app.domain.rules import load_rules
from app.services.messages import format_price

PATIENT = "5581977776666"


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    handoff: bool | None = None
    llm_called: bool | None = None
    offered_slots: bool | None = None
    pending_action: Literal["book", "reschedule", "cancel", "none"] | None = None
    appointments: int | None = None
    approval_requested: bool | None = None
    reply_contains: list[str] = Field(default_factory=list)
    reply_not_contains: list[str] = Field(default_factory=list)


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patient: str
    expect: Expect = Expect()


class SeedBusy(BaseModel):
    start: AwareDatetime
    end: AwareDatetime


class SeedAppointment(BaseModel):
    procedure_id: str
    start: AwareDatetime
    patient_name: str = "Paciente Teste"


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    now: AwareDatetime
    busy: list[SeedBusy] = Field(default_factory=list)
    appointments: list[SeedAppointment] = Field(default_factory=list)
    turns: list[Turn]


@dataclass
class ScenarioResult:
    name: str
    failures: list[str] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failures


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _CountingModel:
    """Counts model requests by wrapping the agent's run (to check 'llm_called')."""

    def __init__(self, container: Container) -> None:
        self.calls = 0
        original = container.agent.run

        async def run(*args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            return await original(*args, **kwargs)

        container.orchestrator.agent = _AgentProxy(container.agent, run)  # type: ignore[assignment]


class _AgentProxy:
    def __init__(self, agent: Any, run: Any) -> None:
        self._agent, self.run = agent, run

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)


def load_scenarios(directory: str | Path) -> list[Scenario]:
    scenarios = []
    for path in sorted(Path(directory).glob("*.yaml")):
        with path.open(encoding="utf-8") as fh:
            scenarios.append(Scenario.model_validate(yaml.safe_load(fh)))
    return scenarios


async def run_scenario(scenario: Scenario, model_spec: str, settings: Settings) -> ScenarioResult:
    started = _time.monotonic()
    result = ScenarioResult(scenario.name)
    rules = load_rules(settings.clinic_config_path)
    clock = _Clock(scenario.now)
    calendar, storage, messaging = FakeCalendar(), InMemoryStorage(), FakeMessaging()
    for b in scenario.busy:
        calendar.add_busy(b.start, b.end)
    for seed in scenario.appointments:
        procedure = rules.procedure(seed.procedure_id)
        assert procedure is not None, f"unknown procedure in scenario: {seed.procedure_id}"
        end = seed.start + timedelta(minutes=procedure.duration_minutes)
        appt = Appointment(
            patient_phone=PATIENT,
            patient_name=seed.patient_name,
            procedure_id=procedure.id,
            start=seed.start,
            end=end,
            created_at=scenario.now - timedelta(days=7),
        )
        appt.calendar_event_id = calendar.add_busy(seed.start, end, "seed")
        await storage.appointments.save(appt)

    model = resolve_model(model_spec, rules=rules)
    container = build_container(
        settings, model=model, calendar=calendar, storage=storage, messaging=messaging, clock=clock
    )
    counter = _CountingModel(container)
    allowed_prices = {p for proc in rules.procedures for p in prices_in(format_price(proc))}

    for i, turn in enumerate(scenario.turns, start=1):
        before_appts = await _active(storage)
        conv_before = await storage.conversations.get(PATIENT)
        pending_before = conv_before.pending_action if conv_before else None
        calls_before = counter.calls
        handled = await container.orchestrator.handle(
            InboundMessage(message_id=f"eval-{i}", from_phone=PATIENT, timestamp=clock.now, text=turn.patient)
        )
        reply = "\n".join(handled.replies)
        result.transcript += [f"  🙂 {turn.patient}", f"  🦷 {reply or '(silêncio)'}"]
        conv = await storage.conversations.get(PATIENT)
        after_appts = await _active(storage)
        prefix = f"turn {i}"

        # ---- universal invariants
        if _appointments_changed(before_appts, after_appts) and pending_before is None:
            result.failures.append(f"{prefix}: agenda alterada sem proposta prévia confirmada")
        bad_prices = prices_in(reply) - allowed_prices
        if bad_prices:
            result.failures.append(f"{prefix}: preço inventado {sorted(bad_prices)}")
        allowed_times = times_in(turn.patient)
        if conv:
            for offered in conv.offered_slots.values():
                allowed_times |= {(offered.slot.start.astimezone(rules.tz).hour, offered.slot.start.minute)}
        for a in [*before_appts, *after_appts]:
            local = a.start.astimezone(rules.tz)
            allowed_times.add((local.hour, local.minute))
        for msg in messaging.outbox:  # anything the code itself sent (tool texts end up here verbatim)
            if msg.kind == "template":
                allowed_times |= times_in(msg.text)
        invented = times_in(reply) - allowed_times - _working_hour_times(rules)
        if invented:
            result.failures.append(f"{prefix}: horário não oferecido pelo código {sorted(invented)}")

        # ---- scenario expectations
        e = turn.expect
        state = conv.state if conv else ConversationState.BOT
        if e.handoff is not None and (state == ConversationState.HUMAN) != e.handoff:
            result.failures.append(f"{prefix}: handoff esperado={e.handoff}, estado={state.value}")
        if e.llm_called is not None and (counter.calls > calls_before) != e.llm_called:
            result.failures.append(f"{prefix}: llm_called esperado={e.llm_called}")
        if e.offered_slots is not None and bool(conv and conv.offered_slots) != e.offered_slots:
            result.failures.append(f"{prefix}: offered_slots esperado={e.offered_slots}")
        if e.pending_action is not None:
            actual = conv.pending_action.action.type if conv and conv.pending_action else "none"
            if actual != e.pending_action:
                result.failures.append(f"{prefix}: pending_action esperado={e.pending_action}, obtido={actual}")
        if e.appointments is not None and len(after_appts) != e.appointments:
            result.failures.append(f"{prefix}: consultas ativas esperadas={e.appointments}, obtidas={len(after_appts)}")
        if e.approval_requested is not None:
            requested = bool(await storage.approvals.list_pending())
            if requested != e.approval_requested:
                result.failures.append(f"{prefix}: approval_requested esperado={e.approval_requested}")
        low = reply.lower()
        result.failures += [
            f"{prefix}: resposta deveria conter '{t}'" for t in e.reply_contains if t.lower() not in low
        ]
        result.failures += [
            f"{prefix}: resposta não deveria conter '{t}'" for t in e.reply_not_contains if t.lower() in low
        ]
        clock.now += timedelta(minutes=2)

    result.seconds = _time.monotonic() - started
    return result


def _working_hour_times(rules: Any) -> set[tuple[int, int]]:
    """Opening hours may legitimately be quoted (they come from get_clinic_info)."""
    out: set[tuple[int, int]] = set()
    for ranges in [*rules.working_hours.values(), *rules.encaixe_windows.values()]:
        for r in ranges:
            out |= {(r.start.hour, r.start.minute), (r.end.hour, r.end.minute)}
    return out


async def _active(storage: InMemoryStorage) -> list[Appointment]:
    return [a for a in await storage.appointments.list_by_patient(PATIENT) if a.is_active]


def _appointments_changed(before: list[Appointment], after: list[Appointment]) -> bool:
    return {(a.id, a.start) for a in before} != {(a.id, a.start) for a in after}


async def run_evals(*, model: str, scenarios_dir: str, only: str | None = None, verbose: bool = False) -> int:
    settings = Settings()
    scenarios = [s for s in load_scenarios(scenarios_dir) if not only or only in s.name]
    if not scenarios:
        print("Nenhum cenário encontrado.")
        return 2
    results = []
    for scenario in scenarios:
        try:
            outcome = await run_scenario(scenario, model, settings)
        except Exception as exc:  # a crash is a failed scenario, not a crashed run
            outcome = ScenarioResult(scenario.name, failures=[f"erro: {type(exc).__name__}: {exc}"])
        results.append(outcome)
        mark = "✅" if outcome.ok else "❌"
        print(f"{mark} {scenario.name} ({outcome.seconds:.1f}s)")
        for failure in outcome.failures:
            print(f"   - {failure}")
        if verbose or not outcome.ok:
            print("\n".join(outcome.transcript))
    passed = sum(r.ok for r in results)
    print(f"\n{passed}/{len(results)} cenários passaram com o modelo '{model}'.")
    return 0 if passed == len(results) else 1
