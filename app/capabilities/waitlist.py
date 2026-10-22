"""Optional capability: waitlist. When a slot frees up, the first patients on the list get a template offer."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Literal

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.domain.events import AppointmentCancelled, AppointmentRescheduled, DomainEvent
from app.domain.models import Appointment, WaitlistEntry, mask_phone
from app.domain.rules import ClinicRules
from app.harness.capability import BaseCapability
from app.harness.context import Services, TurnDeps
from app.harness.notify import Notifier
from app.services.messages import format_when

log = logging.getLogger(__name__)

OFFER_COOLDOWN = timedelta(hours=12)


async def join_waitlist(
    ctx: RunContext[TurnDeps],
    procedure_id: str,
    patient_name: str,
    period: Literal["manha", "tarde", "qualquer"] = "qualquer",
) -> str:
    """Coloca o paciente na lista de espera: ele será avisado se abrir uma vaga antes. Use quando não houver
    horário que sirva ao paciente e ele aceitar esperar.

    Args:
        procedure_id: id do procedimento desejado.
        patient_name: nome completo do paciente.
        period: preferência de período.
    """
    deps = ctx.deps
    procedure = deps.rules.procedure(procedure_id)
    if procedure is None or not procedure.bookable_by_bot:
        return "Procedimento inválido para a lista de espera."
    storage = deps.services.storage
    existing = [e for e in await storage.waitlist.list_active(procedure_id) if e.phone == deps.patient.phone]
    if existing:
        return f"O paciente já está na lista de espera para {procedure.name}."
    await storage.waitlist.save(
        WaitlistEntry(
            phone=deps.patient.phone,
            patient_name=" ".join(patient_name.split()),
            procedure_id=procedure_id,
            period=period,
            created_at=deps.now,
        )
    )
    return (
        f"Paciente incluído na lista de espera para {procedure.name}. Diga que avisaremos por aqui se abrir uma vaga, "
        "sem garantia de horário."
    )


async def leave_waitlist(ctx: RunContext[TurnDeps]) -> str:
    """Remove o paciente de todas as listas de espera."""
    deps = ctx.deps
    removed = 0
    for entry in await deps.services.storage.waitlist.list_active():
        if entry.phone == deps.patient.phone:
            entry.active = False
            await deps.services.storage.waitlist.save(entry)
            removed += 1
    return "Paciente removido da lista de espera." if removed else "O paciente não estava na lista de espera."


class WaitlistCapability(BaseCapability):
    name = "waitlist"

    def __init__(self, rules: ClinicRules, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(rules, config)
        self.max_offers = int(self.config.get("max_offers_per_slot", 3))
        self._toolset = FunctionToolset[TurnDeps](tools=[join_waitlist, leave_waitlist])

    def toolset(self) -> FunctionToolset[TurnDeps]:
        return self._toolset

    def instructions(self) -> str:
        return (
            "## Lista de espera\n"
            "- Se nenhum horário servir, ofereça a lista de espera (join_waitlist). Não prometa vaga.\n"
            "- Se o paciente responder a um aviso de vaga, busque horários normalmente com find_available_slots."
        )

    async def on_event(self, event: DomainEvent, services: Services) -> None:
        if isinstance(event, AppointmentCancelled):
            await self._offer(event.appointment, services)
        elif isinstance(event, AppointmentRescheduled):
            await self._offer(event.previous, services)

    async def _offer(self, freed: Appointment, services: Services) -> None:
        now = services.now()
        if freed.start < now + timedelta(minutes=services.rules.scheduling.min_lead_minutes):
            return
        freed_minutes = (freed.end - freed.start).total_seconds() / 60
        hour = freed.start.astimezone(services.rules.tz).hour
        notifier = Notifier(services)
        sent = 0
        for entry in await services.storage.waitlist.list_active():
            if sent >= self.max_offers:
                break
            procedure = services.rules.procedure(entry.procedure_id)
            if procedure is None or procedure.duration_minutes > freed_minutes or entry.phone == freed.patient_phone:
                continue
            if (entry.period == "manha" and hour >= 12) or (entry.period == "tarde" and hour < 12):
                continue
            if entry.last_offered_at and now - entry.last_offered_at < OFFER_COOLDOWN:
                continue
            ok = await notifier.template_to_patient(
                entry.phone,
                "waitlist_offer",
                patient_name=entry.patient_name.split()[0],
                procedure=procedure.name,
                when=format_when(freed.start, services.rules.tz),
            )
            if ok:
                entry.last_offered_at = now
                await services.storage.waitlist.save(entry)
                sent += 1
                log.info("waitlist offer sent to %s", mask_phone(entry.phone))
