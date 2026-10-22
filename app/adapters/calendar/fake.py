"""In-memory calendar for tests and the CLI."""

from __future__ import annotations

import itertools
from datetime import datetime

from app.adapters.calendar.base import CalendarError, CalendarEvent
from app.domain.models import BusyInterval


class FakeCalendar:
    def __init__(self) -> None:
        self.events: dict[str, CalendarEvent] = {}
        self._ids = itertools.count(1)
        self.fail_next: Exception | None = None

    def add_busy(self, start: datetime, end: datetime, title: str = "Compromisso") -> str:
        """Seed a personal/other event (not created by the bot)."""
        event_id = f"evt-{next(self._ids)}"
        self.events[event_id] = CalendarEvent(appointment_id="", title=title, start=start, end=end)
        return event_id

    def _maybe_fail(self) -> None:
        if self.fail_next is not None:
            exc, self.fail_next = self.fail_next, None
            raise exc

    async def list_busy(self, start: datetime, end: datetime) -> list[BusyInterval]:
        self._maybe_fail()
        return sorted(
            (
                BusyInterval(start=e.start, end=e.end, event_id=eid)
                for eid, e in self.events.items()
                if e.start < end and start < e.end
            ),
            key=lambda b: (b.start, b.end),
        )

    async def create_event(self, event: CalendarEvent) -> str:
        self._maybe_fail()
        event_id = f"evt-{next(self._ids)}"
        self.events[event_id] = event
        return event_id

    async def update_event(self, event_id: str, event: CalendarEvent) -> None:
        self._maybe_fail()
        if event_id not in self.events:
            raise CalendarError("event not found")
        self.events[event_id] = event

    async def delete_event(self, event_id: str) -> None:
        self._maybe_fail()
        self.events.pop(event_id, None)
