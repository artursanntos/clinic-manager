"""Domain events published by the harness after state changes. Capabilities subscribe via ``on_event``."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.domain.models import Appointment


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    at: AwareDatetime


class AppointmentBooked(_Event):
    type: Literal["appointment_booked"] = "appointment_booked"
    appointment: Appointment


class AppointmentRescheduled(_Event):
    type: Literal["appointment_rescheduled"] = "appointment_rescheduled"
    appointment: Appointment
    previous: Appointment


class AppointmentCancelled(_Event):
    type: Literal["appointment_cancelled"] = "appointment_cancelled"
    appointment: Appointment


class HandoffRequested(_Event):
    type: Literal["handoff_requested"] = "handoff_requested"
    phone: str
    reason: str


DomainEvent = AppointmentBooked | AppointmentRescheduled | AppointmentCancelled | HandoffRequested
