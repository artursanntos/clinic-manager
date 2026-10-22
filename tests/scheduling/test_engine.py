from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.models import Slot, SlotKind
from app.domain.rules import ClinicRules, Weekday
from app.scheduling import SlotQuery, check_slot, find_available_slots
from app.scheduling.engine import candidate_slots_for_day
from tests.helpers import at, busy, make_rules, mon, rules_dict

MON = date(2026, 10, 26)
SUNDAY_NIGHT = at(2026, 10, 25, 20, 0)


def q(minutes: int, *, day: date = MON, to: date | None = None, **kw: object) -> SlotQuery:
    return SlotQuery(duration=timedelta(minutes=minutes), from_date=day, to_date=to or day, **kw)  # type: ignore[arg-type]


def starts(slots: list[Slot]) -> list[str]:
    return [s.start.strftime("%H:%M") for s in slots]


def all_day(rules: ClinicRules, minutes: int, busy_list=(), now=SUNDAY_NIGHT, **kw: object) -> list[Slot]:
    return candidate_slots_for_day(rules, MON, list(busy_list), now, q(minutes, **kw))


# --------------------------------------------------------------------------- grid, lunch, end of day


def test_regular_grid_without_busy(rules: ClinicRules) -> None:
    slots = all_day(rules, 30)
    assert starts(slots)[:3] == ["08:00", "08:30", "09:00"]
    assert all(s.kind == SlotKind.REGULAR for s in slots)
    assert all(s.start.tzinfo is not None for s in slots)


def test_lunch_is_never_crossed(rules: ClinicRules) -> None:
    slots = all_day(rules, 60)
    assert "11:00" in starts(slots)  # 11:00-12:00 fits
    assert "11:30" not in starts(slots)  # would end 12:30, inside lunch
    assert "12:00" not in starts(slots) and "13:00" not in starts(slots)
    assert "13:30" in starts(slots)


def test_end_of_day(rules: ClinicRules) -> None:
    slots = all_day(rules, 60)
    assert starts(slots)[-1] == "17:00"


def test_long_procedure_that_does_not_fit_at_end_of_day(rules: ClinicRules) -> None:
    friday = date(2026, 10, 30)  # closes 17:00
    slots = candidate_slots_for_day(rules, friday, [], SUNDAY_NIGHT, q(90, day=friday))
    assert starts(slots)[-1] == "15:30"
    assert all(s.end <= at(2026, 10, 30, 17, 0) for s in slots if s.start.hour >= 13)


def test_sunday_has_no_slots(rules: ClinicRules) -> None:
    sunday = date(2026, 11, 1)
    assert candidate_slots_for_day(rules, sunday, [], SUNDAY_NIGHT, q(30, day=sunday)) == []


def test_holiday_has_no_slots(rules: ClinicRules) -> None:
    finados = date(2026, 11, 2)  # Monday, holiday
    assert candidate_slots_for_day(rules, finados, [], SUNDAY_NIGHT, q(30, day=finados)) == []


def test_closure_has_no_slots() -> None:
    rules = make_rules(closures=[{"start": "2026-10-26", "end": "2026-10-28", "reason": "Férias"}])
    assert all_day(rules, 30) == []


# --------------------------------------------------------------------------- now, lead times


def test_past_and_min_lead(rules: ClinicRules) -> None:
    now = mon(10, 0)  # min lead 120 → earliest 12:00, morning is over
    slots = all_day(rules, 30, now=now)
    assert starts(slots)[0] == "13:30"


def test_min_lead_exact_boundary(rules: ClinicRules) -> None:
    now = mon(8, 0)  # earliest 10:00 exactly
    assert starts(all_day(rules, 30, now=now))[0] == "10:00"


def test_max_lead(rules: ClinicRules) -> None:
    now = SUNDAY_NIGHT
    far = date(2026, 12, 28)  # > 30 days ahead
    assert find_available_slots(rules, [], now, q(30, day=far, to=far + timedelta(days=3))) == []


def test_naive_now_rejected(rules: ClinicRules) -> None:
    naive = SUNDAY_NIGHT.replace(tzinfo=None)
    with pytest.raises(ValueError):
        find_available_slots(rules, [], naive, q(30))


# --------------------------------------------------------------------------- busy time and gaps


def test_busy_blocks_overlapping_slots(rules: ClinicRules) -> None:
    slots = all_day(rules, 30, [busy(mon(9), mon(10))])
    assert "09:00" not in starts(slots) and "09:30" not in starts(slots)
    assert "08:30" in starts(slots) and "10:00" in starts(slots)


def test_fits_exactly_between_two_appointments(rules: ClinicRules) -> None:
    slots = all_day(rules, 30, [busy(mon(9), mon(10)), busy(mon(10, 30), mon(12))])
    assert "10:00" in starts(slots)


def test_buffer_prevents_tight_fit() -> None:
    rules = make_rules(scheduling=rules_dict()["scheduling"] | {"buffer_minutes": 10})
    slots = all_day(rules, 30, [busy(mon(9), mon(10)), busy(mon(10, 40), mon(12))])
    # 10:10-10:40 would leave no buffer before the 10:40 event
    assert not any(mon(10) <= s.start < mon(10, 40) for s in slots)


def test_gap_fill_offers_off_grid_start(rules: ClinicRules) -> None:
    slots = all_day(rules, 30, [busy(mon(8), mon(8, 45))])
    assert "08:45" in starts(slots)


def test_gap_fill_rounds_up_to_five_minutes(rules: ClinicRules) -> None:
    slots = all_day(rules, 30, [busy(mon(8), mon(8, 42))])
    assert "08:45" in starts(slots) and "08:42" not in starts(slots)


def test_gap_fill_can_be_disabled() -> None:
    rules = make_rules(scheduling=rules_dict()["scheduling"] | {"fill_gaps": False})
    assert "08:45" not in starts(all_day(rules, 30, [busy(mon(8), mon(8, 45))]))


def test_ignore_event_ids_when_rescheduling(rules: ClinicRules) -> None:
    b = [busy(mon(9), mon(10), event_id="evt-own")]
    slots = all_day(rules, 60, b, ignore_event_ids=frozenset({"evt-own"}))
    assert "09:00" in starts(slots)


# --------------------------------------------------------------------------- encaixe windows


def test_encaixe_not_offered_by_default(rules: ClinicRules) -> None:
    assert "12:00" not in starts(all_day(rules, 30))


def test_encaixe_window_slot(rules: ClinicRules) -> None:
    slots = all_day(rules, 30, include_encaixe=True)
    noon = [s for s in slots if s.start == mon(12)]
    assert len(noon) == 1 and noon[0].kind == SlotKind.ENCAIXE


def test_slot_spanning_regular_and_encaixe_is_encaixe(rules: ClinicRules) -> None:
    slots = all_day(rules, 60, include_encaixe=True)
    s = next(s for s in slots if s.start == mon(11, 30))
    assert s.kind == SlotKind.ENCAIXE


def test_regular_wins_over_encaixe(rules: ClinicRules) -> None:
    slots = all_day(rules, 30, include_encaixe=True)
    assert next(s for s in slots if s.start == mon(8)).kind == SlotKind.REGULAR


# --------------------------------------------------------------------------- find_available_slots (spread, filters)


def test_spread_and_limit(rules: ClinicRules) -> None:
    slots = find_available_slots(rules, [], SUNDAY_NIGHT, q(30, to=MON + timedelta(days=6)))
    assert len(slots) == 6  # max_options
    by_day: dict[date, int] = {}
    for s in slots:
        by_day[s.start.date()] = by_day.get(s.start.date(), 0) + 1
    assert max(by_day.values()) <= 3
    first_day = [s for s in slots if s.start.date() == MON]
    assert starts(first_day)[0] == "08:00" and starts(first_day)[-1] == "17:30"


def test_period_filter(rules: ClinicRules) -> None:
    slots = find_available_slots(rules, [], SUNDAY_NIGHT, q(30, to=MON + timedelta(days=6), period="tarde"))
    assert slots and all(s.start.hour >= 12 for s in slots)


def test_weekday_filter(rules: ClinicRules) -> None:
    slots = find_available_slots(
        rules,
        [],
        SUNDAY_NIGHT,
        q(30, to=MON + timedelta(days=6), weekdays=frozenset({Weekday.WEDNESDAY})),
    )
    assert slots and {s.start.date() for s in slots} == {date(2026, 10, 28)}


def test_encaixe_appended_once_per_day(rules: ClinicRules) -> None:
    slots = find_available_slots(rules, [], SUNDAY_NIGHT, q(30, include_encaixe=True, limit=10))
    assert [s.kind for s in slots].count(SlotKind.ENCAIXE) == 1


def test_deterministic(rules: ClinicRules) -> None:
    b = [busy(mon(9), mon(10)), busy(mon(14), mon(15, 20))]
    query = q(30, to=MON + timedelta(days=10))
    assert find_available_slots(rules, b, SUNDAY_NIGHT, query) == find_available_slots(
        rules, list(reversed(b)), SUNDAY_NIGHT, query
    )


def test_fully_booked_day_returns_next_days(rules: ClinicRules) -> None:
    b = [busy(mon(8), mon(18))]
    slots = find_available_slots(rules, b, SUNDAY_NIGHT, q(30, to=MON + timedelta(days=2)))
    assert slots and all(s.start.date() > MON for s in slots)


# --------------------------------------------------------------------------- check_slot (revalidation on write)


def slot(start_h: int, start_m: int, minutes: int, kind: SlotKind = SlotKind.REGULAR) -> Slot:
    start = mon(start_h, start_m)
    return Slot(start=start, end=start + timedelta(minutes=minutes), kind=kind)


def test_check_slot_ok(rules: ClinicRules) -> None:
    result = check_slot(rules, slot(9, 0, 30), [], SUNDAY_NIGHT)
    assert result.ok and result.kind == SlotKind.REGULAR


def test_check_slot_taken_meanwhile(rules: ClinicRules) -> None:
    result = check_slot(rules, slot(9, 0, 30), [busy(mon(9, 15), mon(9, 45))], SUNDAY_NIGHT)
    assert not result.ok and result.reason == "horário não está mais livre"


def test_check_slot_outside_hours(rules: ClinicRules) -> None:
    assert not check_slot(rules, slot(19, 0, 30), [], SUNDAY_NIGHT).ok


def test_check_slot_encaixe_kind(rules: ClinicRules) -> None:
    result = check_slot(rules, slot(12, 0, 30), [], SUNDAY_NIGHT)
    assert result.ok and result.kind == SlotKind.ENCAIXE


def test_check_slot_in_the_past(rules: ClinicRules) -> None:
    assert not check_slot(rules, slot(9, 0, 30), [], mon(9, 30)).ok


def test_check_slot_holiday(rules: ClinicRules) -> None:
    start = at(2026, 11, 2, 9)
    s = Slot(start=start, end=start + timedelta(minutes=30))
    result = check_slot(rules, s, [], SUNDAY_NIGHT)
    assert not result.ok and "feriado" in (result.reason or "")


def test_check_slot_ignores_own_event(rules: ClinicRules) -> None:
    b = [busy(mon(9), mon(10), event_id="mine")]
    assert check_slot(rules, slot(9, 0, 60), b, SUNDAY_NIGHT, ignore_event_ids=frozenset({"mine"})).ok
