"""Calendar provider protocol. The dentist's calendar is the source of truth for busy time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.models import BusyInterval

APPOINTMENT_ID_KEY = "clinic_appointment_id"
"""Name of the private metadata field that links a calendar event to an appointment."""


class CalendarError(Exception):
    """Any provider failure (network, auth, quota). Messages must not contain patient data."""


@dataclass(frozen=True)
class CalendarEvent:
    """What the bot writes to the calendar. Minimal patient data (LGPD): name + procedure, never the phone."""

    appointment_id: str
    title: str
    start: datetime
    end: datetime
    description: str = ""


@runtime_checkable
class CalendarProvider(Protocol):
    async def list_busy(self, start: datetime, end: datetime) -> list[BusyInterval]:
        """Every event between ``start`` and ``end`` blocks time (personal events and expanded recurrences too)."""
        ...

    async def create_event(self, event: CalendarEvent) -> str:
        """Create the event and return the provider's event id."""
        ...

    async def update_event(self, event_id: str, event: CalendarEvent) -> None: ...

    async def delete_event(self, event_id: str) -> None:
        """Delete the event. Deleting an event that no longer exists is not an error."""
        ...
