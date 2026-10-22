from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from icalendar import Event

from app.adapters.calendar.base import APPOINTMENT_ID_KEY, CalendarEvent
from app.adapters.calendar.caldav import CalDavCalendar, build_ical, busy_from_components
from app.adapters.calendar.fake import FakeCalendar
from app.adapters.calendar.google import GoogleCalendar, parse_google_event
from app.adapters.calendar.microsoft import (
    EXTENDED_PROPERTY_ID,
    MicrosoftCalendar,
    MicrosoftTokenProvider,
    parse_graph_event,
)
from tests.adapters.calendar.contract import check_calendar_contract
from tests.adapters.calendar.fake_servers import FakeGoogleApi, FakeGraphApi

TZ = ZoneInfo("America/Recife")
DAY = datetime(2026, 10, 26, tzinfo=TZ)


async def static_token() -> str:
    return "google-token"


def google_with(api: FakeGoogleApi) -> GoogleCalendar:
    client = httpx.AsyncClient(transport=httpx.MockTransport(api))
    return GoogleCalendar(calendar_id="agenda@group.calendar.google.com", token_source=static_token, client=client)


def graph_with(api: FakeGraphApi, *, user_id: str | None = None) -> MicrosoftCalendar:
    client = httpx.AsyncClient(transport=httpx.MockTransport(api))
    tokens = MicrosoftTokenProvider(
        mode="refresh_token", tenant_id="consumers", client_id="cid", refresh_token="rt", client=client
    )
    return MicrosoftCalendar(tokens, user_id=user_id, client=client)


class _MemoryCalDav:
    """CalDavBackend that stores raw iCalendar text (exercises build_ical + parsing)."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def events_between(self, start: datetime, end: datetime) -> list[Any]:
        from icalendar import Calendar

        out = []
        for ical in self.items.values():
            for comp in Calendar.from_ical(ical).walk("VEVENT"):
                if comp.decoded("DTSTART") < end and start < comp.decoded("DTEND"):
                    out.append(comp)
        return out

    def add(self, ical: str) -> None:
        from icalendar import Calendar

        uid = str(next(iter(Calendar.from_ical(ical).walk("VEVENT")))["UID"])
        self.items[uid] = ical

    def replace(self, uid: str, ical: str) -> bool:
        if uid not in self.items:
            return False
        self.items[uid] = ical
        return True

    def delete(self, uid: str) -> bool:
        return self.items.pop(uid, None) is not None


# --------------------------------------------------------------------------- contract on every provider


async def test_contract_fake() -> None:
    await check_calendar_contract(FakeCalendar(), DAY)


async def test_contract_google_over_mocked_http() -> None:
    api = FakeGoogleApi()
    await check_calendar_contract(google_with(api), DAY)
    assert "agenda%40group.calendar.google.com" in str(api.requests[0].url)


async def test_contract_microsoft_over_mocked_http() -> None:
    await check_calendar_contract(graph_with(FakeGraphApi()), DAY)


async def test_contract_caldav_with_memory_backend() -> None:
    await check_calendar_contract(CalDavCalendar(backend=_MemoryCalDav()), DAY)


# --------------------------------------------------------------------------- provider specifics


async def test_google_writes_private_appointment_id_and_no_phone() -> None:
    api = FakeGoogleApi()
    start = DAY + timedelta(hours=9)
    await google_with(api).create_event(
        CalendarEvent(
            appointment_id="a1", title="Limpeza — Ana (WhatsApp)", start=start, end=start + timedelta(hours=1)
        )
    )
    (stored,) = api.events.values()
    assert stored["extendedProperties"]["private"][APPOINTMENT_ID_KEY] == "a1"


def test_google_parsing_rules() -> None:
    assert parse_google_event({"id": "x", "status": "cancelled", "start": {}, "end": {}}, TZ) is None
    free = {
        "id": "x",
        "transparency": "transparent",
        "start": {"dateTime": "2026-10-26T09:00:00-03:00"},
        "end": {"dateTime": "2026-10-26T10:00:00-03:00"},
    }
    assert parse_google_event(free, TZ) is None
    all_day = parse_google_event({"id": "d", "start": {"date": "2026-10-26"}, "end": {"date": "2026-10-27"}}, TZ)
    assert all_day is not None and all_day.start == DAY and all_day.end == DAY + timedelta(days=1)


async def test_google_http_error_becomes_calendar_error() -> None:
    from app.adapters.calendar.base import CalendarError

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={})))
    cal = GoogleCalendar(calendar_id="x", token_source=static_token, client=client)
    with pytest.raises(CalendarError):
        await cal.list_busy(DAY, DAY + timedelta(days=1))


async def test_graph_extended_property_and_user_path() -> None:
    api = FakeGraphApi()
    cal = graph_with(api, user_id="dra@clinica.com")
    start = DAY + timedelta(hours=9)
    await cal.create_event(CalendarEvent(appointment_id="a9", title="t", start=start, end=start + timedelta(hours=1)))
    (stored,) = api.events.values()
    assert stored["singleValueExtendedProperties"] == [{"id": EXTENDED_PROPERTY_ID, "value": "a9"}]
    assert any("/users/dra@clinica.com/" in str(r.url) for r in api.requests)


def test_graph_parsing_rules() -> None:
    base = {
        "id": "e",
        "start": {"dateTime": "2026-10-26T12:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-10-26T13:00:00.0000000", "timeZone": "UTC"},
    }
    parsed = parse_graph_event(base, TZ)
    assert parsed is not None and parsed.start == DAY + timedelta(hours=9)  # 12:00 UTC = 09:00 Recife
    assert parse_graph_event({**base, "showAs": "free"}, TZ) is None
    assert parse_graph_event({**base, "isCancelled": True}, TZ) is None
    all_day = parse_graph_event(
        {
            "id": "d",
            "isAllDay": True,
            "start": {"dateTime": "2026-10-26T00:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-10-27T00:00:00.0000000", "timeZone": "UTC"},
        },
        TZ,
    )
    assert all_day is not None and all_day.start == DAY


def test_token_provider_requires_credentials() -> None:
    with pytest.raises(ValueError):
        MicrosoftTokenProvider(mode="client_credentials", tenant_id="t", client_id="c")
    with pytest.raises(ValueError):
        MicrosoftTokenProvider(mode="refresh_token", tenant_id="t", client_id="c")


def test_caldav_busy_rules() -> None:
    def vevent(**props: Any) -> Event:
        e = Event()
        for k, v in props.items():
            e.add(k, v)
        return e

    start = DAY + timedelta(hours=9)
    comps = [
        vevent(uid="a", dtstart=start, dtend=start + timedelta(hours=1)),
        vevent(uid="b", dtstart=start, duration=timedelta(minutes=30)),
        vevent(uid="c", dtstart=start, dtend=start + timedelta(hours=1), transp="TRANSPARENT"),
        vevent(uid="d", dtstart=start, dtend=start + timedelta(hours=1), status="CANCELLED"),
        vevent(uid="e", dtstart=date(2026, 10, 27)),  # all-day, no end → one day
        vevent(
            uid="f",
            dtstart=datetime.combine(date(2026, 10, 28), time(10)),
            dtend=datetime.combine(date(2026, 10, 28), time(11)),
        ),  # floating
    ]
    busy = {b.event_id: b for b in busy_from_components(comps, TZ)}
    assert set(busy) == {"a", "b", "e", "f"}
    assert busy["b"].end == start + timedelta(minutes=30)
    assert busy["e"].end - busy["e"].start == timedelta(days=1)
    assert busy["f"].start.tzinfo == TZ


def test_build_ical_round_trip() -> None:
    from icalendar import Calendar

    start = DAY + timedelta(hours=9)
    ical = build_ical(
        "uid-1", CalendarEvent(appointment_id="a1", title="t", start=start, end=start + timedelta(hours=1))
    )
    (comp,) = Calendar.from_ical(ical).walk("VEVENT")
    assert str(comp["X-CLINIC-APPOINTMENT-ID"]) == "a1" and comp.decoded("DTSTART") == start


# --------------------------------------------------------------------------- real services (opt-in)


def far_future_day() -> datetime:
    """A day far enough ahead to be empty in a real calendar."""
    return datetime.combine(datetime.now(TZ).date() + timedelta(days=400), time.min, tzinfo=TZ)


@pytest.mark.integration
async def test_contract_google_real() -> None:
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID")
    if not calendar_id:
        pytest.skip("GOOGLE_CALENDAR_ID not set")
    cal = GoogleCalendar(calendar_id=calendar_id, credentials_file=os.environ.get("GOOGLE_CREDENTIALS_FILE"))
    await check_calendar_contract(cal, far_future_day())


@pytest.mark.integration
async def test_contract_caldav_real() -> None:
    if not os.environ.get("CALDAV_URL"):
        pytest.skip("CALDAV_URL not set")
    cal = CalDavCalendar(
        url=os.environ["CALDAV_URL"],
        username=os.environ.get("CALDAV_USERNAME"),
        password=os.environ.get("CALDAV_PASSWORD"),
        calendar_name=os.environ.get("CALDAV_CALENDAR_NAME"),
    )
    await check_calendar_contract(cal, far_future_day())
