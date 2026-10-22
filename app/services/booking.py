"""Booking use cases. Every write re-validates the slot against the real calendar right before writing."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from app.adapters.calendar.base import CalendarError, CalendarEvent, CalendarProvider
from app.adapters.storage.base import Storage
from app.domain.events import AppointmentBooked, AppointmentCancelled, AppointmentRescheduled, DomainEvent
from app.domain.models import (
    Appointment,
    AppointmentStatus,
    BookAction,
    BusyInterval,
    CancelAction,
    DomainAction,
    Patient,
    RescheduleAction,
    Slot,
)
from app.domain.rules import ClinicRules
from app.scheduling import SlotQuery, check_slot, find_available_slots
from app.services.messages import format_when

log = logging.getLogger(__name__)


@dataclass
class ActionOutcome:
    ok: bool
    message: str
    """Canonical PT-BR text describing the result (safe to send to the patient)."""
    appointment: Appointment | None = None
    events: list[DomainEvent] = field(default_factory=list)


class BookingService:
    def __init__(self, rules: ClinicRules, calendar: CalendarProvider, storage: Storage) -> None:
        self.rules = rules
        self.calendar = calendar
        self.storage = storage

    # ------------------------------------------------------------------ reads

    async def busy_between(self, from_date: date, to_date: date) -> list[BusyInterval]:
        tz = self.rules.tz
        start = datetime.combine(from_date, time.min, tzinfo=tz)
        end = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=tz)
        return await self.calendar.list_busy(start, end)

    async def find_slots(self, query: SlotQuery, now: datetime) -> list[Slot]:
        busy = await self.busy_between(query.from_date, query.to_date)
        return find_available_slots(self.rules, busy, now, query)

    async def upcoming_for(self, phone: str, now: datetime) -> list[Appointment]:
        return [a for a in await self.storage.appointments.list_by_patient(phone) if a.is_active and a.end > now]

    # ------------------------------------------------------------------ writes

    async def execute(self, action: DomainAction, phone: str, now: datetime) -> ActionOutcome:
        try:
            if isinstance(action, BookAction):
                return await self._book(action, phone, now)
            if isinstance(action, RescheduleAction):
                return await self._reschedule(action, phone, now)
            return await self._cancel(action, phone, now)
        except CalendarError:
            log.exception("calendar failure while executing %s", action.type)
            return ActionOutcome(
                ok=False, message="Não consegui acessar a agenda agora. Pode tentar de novo em alguns minutos?"
            )

    async def _revalidate(self, slot: Slot, now: datetime, ignore: frozenset[str] = frozenset()) -> str | None:
        day = slot.start.astimezone(self.rules.tz).date()
        busy = await self.busy_between(day, day)
        result = check_slot(self.rules, slot, busy, now, ignore_event_ids=ignore)
        return None if result.ok else (result.reason or "horário indisponível")

    def _event(self, appointment: Appointment) -> CalendarEvent:
        procedure = self.rules.procedure(appointment.procedure_id)
        name = procedure.name if procedure else appointment.procedure_id
        suffix = " [ENCAIXE]" if appointment.kind == "encaixe" else ""
        return CalendarEvent(
            appointment_id=appointment.id,
            title=f"{name} — {appointment.patient_name} (WhatsApp){suffix}",
            start=appointment.start,
            end=appointment.end,
            description=f"Agendado pelo assistente do WhatsApp. Código: {appointment.id}",
        )

    async def _book(self, action: BookAction, phone: str, now: datetime) -> ActionOutcome:
        procedure = self.rules.procedure(action.procedure_id)
        if procedure is None or not procedure.bookable_by_bot:
            return ActionOutcome(ok=False, message="Esse procedimento precisa ser agendado diretamente com a doutora.")
        problem = await self._revalidate(action.slot, now)
        if problem:
            return ActionOutcome(ok=False, message=f"Esse horário não está mais disponível ({problem}).")

        appointment = Appointment(
            patient_phone=phone,
            patient_name=action.patient_name,
            procedure_id=procedure.id,
            start=action.slot.start,
            end=action.slot.end,
            kind=action.slot.kind,
            created_at=now,
        )
        appointment.calendar_event_id = await self.calendar.create_event(self._event(appointment))
        try:
            await self.storage.appointments.save(appointment)
        except Exception:
            await self.calendar.delete_event(appointment.calendar_event_id)
            raise
        patient = await self.storage.patients.get(phone) or Patient(phone=phone, created_at=now)
        patient.name = action.patient_name
        await self.storage.patients.save(patient)
        return ActionOutcome(
            ok=True,
            message=f"Consulta marcada: {procedure.name}, {format_when(appointment.start, self.rules.tz)}. "
            f"Código: {appointment.id}.",
            appointment=appointment,
            events=[AppointmentBooked(at=now, appointment=appointment)],
        )

    async def _owned_active(self, appointment_id: str, phone: str) -> Appointment | None:
        appointment = await self.storage.appointments.get(appointment_id)
        if appointment is None or appointment.patient_phone != phone or not appointment.is_active:
            return None
        return appointment

    async def _reschedule(self, action: RescheduleAction, phone: str, now: datetime) -> ActionOutcome:
        current = await self._owned_active(action.appointment_id, phone)
        if current is None:
            return ActionOutcome(ok=False, message="Não encontrei essa consulta ativa no seu nome.")
        ignore = frozenset({current.calendar_event_id}) if current.calendar_event_id else frozenset()
        problem = await self._revalidate(action.slot, now, ignore)
        if problem:
            return ActionOutcome(ok=False, message=f"Esse horário não está mais disponível ({problem}).")

        previous = current.model_copy(deep=True)
        current.start, current.end, current.kind = action.slot.start, action.slot.end, action.slot.kind
        current.confirmed_at = None
        current.reminder_sent_at = None
        if current.calendar_event_id:
            await self.calendar.update_event(current.calendar_event_id, self._event(current))
        else:
            current.calendar_event_id = await self.calendar.create_event(self._event(current))
        await self.storage.appointments.save(current)
        return ActionOutcome(
            ok=True,
            message=f"Consulta remarcada para {format_when(current.start, self.rules.tz)}. Código: {current.id}.",
            appointment=current,
            events=[AppointmentRescheduled(at=now, appointment=current, previous=previous)],
        )

    async def _cancel(self, action: CancelAction, phone: str, now: datetime) -> ActionOutcome:
        current = await self._owned_active(action.appointment_id, phone)
        if current is None:
            return ActionOutcome(ok=False, message="Não encontrei essa consulta ativa no seu nome.")
        if current.calendar_event_id:
            await self.calendar.delete_event(current.calendar_event_id)
        current.status = AppointmentStatus.CANCELLED
        current.cancelled_at = now
        await self.storage.appointments.save(current)
        return ActionOutcome(
            ok=True,
            message=f"Consulta de {format_when(current.start, self.rules.tz)} cancelada.",
            appointment=current,
            events=[AppointmentCancelled(at=now, appointment=current)],
        )
