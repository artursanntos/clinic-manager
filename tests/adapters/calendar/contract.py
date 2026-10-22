"""Shared contract every CalendarProvider must satisfy. Used with fakes, mocked HTTP and (opt-in) real services."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.adapters.calendar.base import CalendarEvent, CalendarProvider


async def check_calendar_contract(provider: CalendarProvider, day_start: datetime) -> None:
    """``day_start``: an aware datetime at the beginning of a day with no other events in [day_start, +1 day)."""
    window_end = day_start + timedelta(days=1)
    start = day_start + timedelta(hours=9)
    event = CalendarEvent(
        appointment_id="appt-contract-1",
        title="Avaliação — Contrato (WhatsApp)",
        start=start,
        end=start + timedelta(minutes=30),
        description="teste de contrato",
    )

    event_id = await provider.create_event(event)
    assert isinstance(event_id, str) and event_id

    busy = await provider.list_busy(day_start, window_end)
    mine = [b for b in busy if b.event_id == event_id]
    assert len(mine) == 1, busy
    assert mine[0].start == event.start and mine[0].end == event.end
    assert all(b.start.tzinfo is not None for b in busy)

    moved = CalendarEvent(
        appointment_id=event.appointment_id,
        title=event.title,
        start=start + timedelta(hours=2),
        end=start + timedelta(hours=2, minutes=30),
    )
    await provider.update_event(event_id, moved)
    busy = await provider.list_busy(day_start, window_end)
    mine = [b for b in busy if b.event_id == event_id]
    assert len(mine) == 1 and mine[0].start == moved.start

    outside = await provider.list_busy(window_end, window_end + timedelta(days=1))
    assert all(b.event_id != event_id for b in outside)

    await provider.delete_event(event_id)
    assert all(b.event_id != event_id for b in await provider.list_busy(day_start, window_end))
    await provider.delete_event(event_id)  # idempotent
