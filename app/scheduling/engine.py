"""Deterministic slot engine.

Pure functions: no I/O, no clock. ``now`` and the calendar's busy intervals are inputs. The same inputs always
produce the same output, in the same order.

Definitions:
- A **regular** slot fits entirely inside one working-hours range of its day.
- An **encaixe** slot is not regular but fits entirely inside the union of working hours and encaixe windows
  (e.g. the end of lunch). Booking one may require the dentist's approval.
- A slot never overlaps a busy interval, keeping ``buffer_minutes`` of free time on both sides.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

from app.domain.models import BusyInterval, Slot, SlotKind
from app.domain.rules import ClinicRules, TimeRange, Weekday

Period = Literal["manha", "tarde", "noite", "qualquer"]

_GAP_ROUNDING = timedelta(minutes=5)
_NOON = time(12, 0)
_EVENING = time(18, 0)


@dataclass(frozen=True)
class SlotQuery:
    duration: timedelta
    from_date: date
    to_date: date
    """Inclusive."""
    period: Period = "qualquer"
    weekdays: frozenset[Weekday] | None = None
    include_encaixe: bool = False
    ignore_event_ids: frozenset[str] = frozenset()
    """Busy intervals with these event ids are ignored (used when rescheduling an existing appointment)."""
    limit: int | None = None


@dataclass(frozen=True)
class SlotCheck:
    ok: bool
    kind: SlotKind | None = None
    reason: str | None = None


# --------------------------------------------------------------------------- helpers


def _ranges_for(rules: ClinicRules, day: date, *, include_encaixe: bool) -> list[tuple[datetime, datetime]]:
    tz = rules.tz
    weekday = Weekday.from_date(day)
    ranges: list[TimeRange] = list(rules.working_hours.get(weekday, []))
    if include_encaixe:
        ranges += rules.encaixe_windows.get(weekday, [])
    intervals = sorted(
        (datetime.combine(day, r.start, tzinfo=tz), datetime.combine(day, r.end, tzinfo=tz)) for r in ranges
    )
    # merge touching/overlapping ranges so a slot can span e.g. "end of lunch" + "afternoon"
    merged: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _contained(start: datetime, end: datetime, ranges: Iterable[tuple[datetime, datetime]]) -> bool:
    return any(rs <= start and end <= re for rs, re in ranges)


def _ceil_to(dt: datetime, step: timedelta) -> datetime:
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = dt - midnight
    steps = math.ceil(elapsed / step)
    return midnight + steps * step


def _conflicts(
    start: datetime, end: datetime, busy: Sequence[BusyInterval], buffer: timedelta, ignore: frozenset[str]
) -> bool:
    for b in busy:
        if b.event_id is not None and b.event_id in ignore:
            continue
        if start < b.end + buffer and b.start < end + buffer:
            return True
    return False


def _day_is_closed(rules: ClinicRules, day: date) -> str | None:
    holiday = rules.holiday_on(day)
    if holiday:
        return f"feriado ({holiday.name})"
    closure = rules.closure_on(day)
    if closure:
        return closure.reason
    return None


def _matches_period(start: datetime, period: Period) -> bool:
    t = start.timetz().replace(tzinfo=None)
    if period == "manha":
        return t < _NOON
    if period == "tarde":
        return _NOON <= t < _EVENING
    if period == "noite":
        return t >= _EVENING
    return True


def _spread(items: Sequence[Slot], k: int) -> list[Slot]:
    """Pick ``k`` items evenly spread over the list (deterministic), keeping order."""
    n = len(items)
    if n <= k:
        return list(items)
    if k == 1:
        return [items[0]]
    idx = sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})
    return [items[i] for i in idx]


def _bounds(rules: ClinicRules, now: datetime) -> tuple[datetime, datetime]:
    s = rules.scheduling
    return now + timedelta(minutes=s.min_lead_minutes), now + timedelta(days=s.max_lead_days)


# --------------------------------------------------------------------------- public API


def candidate_slots_for_day(
    rules: ClinicRules, day: date, busy: Sequence[BusyInterval], now: datetime, query: SlotQuery
) -> list[Slot]:
    """Every valid slot of one day (no spreading/limit), sorted by start."""
    if _day_is_closed(rules, day):
        return []
    s = rules.scheduling
    step = timedelta(minutes=s.slot_step_minutes)
    buffer = timedelta(minutes=s.buffer_minutes)
    earliest, latest = _bounds(rules, now)

    regular_ranges = _ranges_for(rules, day, include_encaixe=False)
    all_ranges = _ranges_for(rules, day, include_encaixe=query.include_encaixe)

    starts: set[datetime] = set()
    for rs, re in all_ranges:
        t = rs
        while t + query.duration <= re:
            starts.add(t)
            t += step
        if s.fill_gaps:
            for b in busy:
                gap_start = _ceil_to(b.end + buffer, _GAP_ROUNDING)
                if rs <= gap_start and gap_start + query.duration <= re:
                    starts.add(gap_start)

    slots: list[Slot] = []
    for start in sorted(starts):
        end = start + query.duration
        if start < earliest or start > latest:
            continue
        if not _matches_period(start, query.period):
            continue
        if _conflicts(start, end, busy, buffer, query.ignore_event_ids):
            continue
        if _contained(start, end, regular_ranges):
            kind = SlotKind.REGULAR
        elif query.include_encaixe and _contained(start, end, all_ranges):
            kind = SlotKind.ENCAIXE
        else:
            continue
        slots.append(Slot(start=start, end=end, kind=kind))
    return slots


def find_available_slots(
    rules: ClinicRules, busy: Sequence[BusyInterval], now: datetime, query: SlotQuery
) -> list[Slot]:
    """Available slots spread across days.

    Per day: up to ``max_options_per_day`` regular slots evenly spread over the day, plus (if requested) the first
    encaixe slot. Stops at ``query.limit`` (default ``max_options``). Sorted by start.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    s = rules.scheduling
    limit = query.limit or s.max_options
    _, latest = _bounds(rules, now)
    last_day = min(query.to_date, latest.astimezone(rules.tz).date())

    result: list[Slot] = []
    day = query.from_date
    while day <= last_day and len(result) < limit:
        if query.weekdays is None or Weekday.from_date(day) in query.weekdays:
            day_slots = candidate_slots_for_day(rules, day, busy, now, query)
            regular = [x for x in day_slots if x.kind == SlotKind.REGULAR]
            picked = _spread(regular, s.max_options_per_day)
            encaixe = [x for x in day_slots if x.kind == SlotKind.ENCAIXE]
            if encaixe:
                picked.append(encaixe[0])
            result.extend(sorted(picked, key=lambda x: (x.start, x.end)))
        day += timedelta(days=1)
    return result[:limit]


def check_slot(
    rules: ClinicRules,
    slot: Slot,
    busy: Sequence[BusyInterval],
    now: datetime,
    *,
    ignore_event_ids: frozenset[str] = frozenset(),
) -> SlotCheck:
    """Re-validate a slot right before writing it (does not require grid alignment)."""
    tz = rules.tz
    start = slot.start.astimezone(tz)
    end = slot.end.astimezone(tz)
    day = start.date()
    if end.date() != day:
        return SlotCheck(ok=False, reason="o horário atravessa a meia-noite")
    closed = _day_is_closed(rules, day)
    if closed:
        return SlotCheck(ok=False, reason=f"dia fechado: {closed}")
    earliest, latest = _bounds(rules, now)
    if start < earliest:
        return SlotCheck(ok=False, reason="horário já passou ou está muito próximo")
    if start > latest:
        return SlotCheck(ok=False, reason="horário além da antecedência máxima")
    buffer = timedelta(minutes=rules.scheduling.buffer_minutes)
    if _conflicts(start, end, busy, buffer, ignore_event_ids):
        return SlotCheck(ok=False, reason="horário não está mais livre")
    if _contained(start, end, _ranges_for(rules, day, include_encaixe=False)):
        return SlotCheck(ok=True, kind=SlotKind.REGULAR)
    if _contained(start, end, _ranges_for(rules, day, include_encaixe=True)):
        return SlotCheck(ok=True, kind=SlotKind.ENCAIXE)
    return SlotCheck(ok=False, reason="fora do horário de atendimento")
