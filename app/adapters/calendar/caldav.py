"""CalDAV calendars (iCloud, Nextcloud, Fastmail, Zimbra…) via the ``caldav`` library.

The library is synchronous; calls run in a worker thread. The provider event id is the iCalendar UID.
iCloud: URL ``https://caldav.icloud.com``, Apple ID e-mail as username and an *app-specific password*
(appleid.apple.com → Sign-In and Security → App-Specific Passwords). Install with ``uv sync --extra caldav``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from app.adapters.calendar.base import CalendarError, CalendarEvent
from app.domain.models import BusyInterval

X_APPOINTMENT_ID = "X-CLINIC-APPOINTMENT-ID"
PRODID = "-//clinic-manager//PT-BR"


class CalDavBackend(Protocol):
    """Minimal sync surface over the caldav library (swappable in tests)."""

    def events_between(self, start: datetime, end: datetime) -> list[Any]:
        """VEVENT components (recurrences expanded)."""
        ...

    def add(self, ical: str) -> None: ...
    def replace(self, uid: str, ical: str) -> bool: ...
    def delete(self, uid: str) -> bool: ...


class _LibraryBackend:
    def __init__(self, url: str, username: str, password: str, calendar_name: str | None) -> None:
        from caldav.davclient import DAVClient

        self._client = cast(Any, DAVClient(url=url, username=username, password=password))
        calendars = self._client.principal().calendars()
        if not calendars:
            raise CalendarError("no CalDAV calendars found for this account")
        if calendar_name:
            matches = [c for c in calendars if (c.get_display_name() or "") == calendar_name]
            if not matches:
                raise CalendarError("CalDAV calendar not found (check CALDAV_CALENDAR_NAME)")
            self._calendar = matches[0]
        else:
            self._calendar = calendars[0]

    def events_between(self, start: datetime, end: datetime) -> list[Any]:
        objects = self._calendar.search(start=start, end=end, event=True, expand=True)
        return [obj.icalendar_component for obj in objects]

    def add(self, ical: str) -> None:
        self._calendar.add_event(ical)

    def _find(self, uid: str) -> Any | None:
        from caldav.lib.error import NotFoundError

        try:
            return self._calendar.get_event_by_uid(uid)
        except NotFoundError:
            return None

    def replace(self, uid: str, ical: str) -> bool:
        existing = self._find(uid)
        if existing is None:
            return False
        existing.data = ical
        existing.save()
        return True

    def delete(self, uid: str) -> bool:
        existing = self._find(uid)
        if existing is None:
            return False
        existing.delete()
        return True


class CalDavCalendar:
    def __init__(
        self,
        *,
        url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        calendar_name: str | None = None,
        timezone: str = "America/Recife",
        backend: CalDavBackend | None = None,
    ) -> None:
        self._tz = ZoneInfo(timezone)
        self._backend_args = (url, username, password, calendar_name)
        self._backend: CalDavBackend | None = backend
        self._lock = asyncio.Lock()

    async def _get_backend(self) -> CalDavBackend:
        async with self._lock:
            if self._backend is None:
                url, username, password, name = self._backend_args
                if not (url and username and password):
                    raise CalendarError("CalDAV needs CALDAV_URL, CALDAV_USERNAME and CALDAV_PASSWORD")
                backend: CalDavBackend = await self._run(_LibraryBackend, url, username, password, name)
                self._backend = backend
            return self._backend

    @staticmethod
    async def _run[T](fn: Any, *args: Any) -> T:
        try:
            result: T = await asyncio.to_thread(fn, *args)
            return result
        except CalendarError:
            raise
        except Exception as exc:
            raise CalendarError(f"caldav failure: {type(exc).__name__}") from exc

    async def list_busy(self, start: datetime, end: datetime) -> list[BusyInterval]:
        backend = await self._get_backend()
        components: list[Any] = await self._run(backend.events_between, start, end)
        return busy_from_components(components, self._tz)

    async def create_event(self, event: CalendarEvent) -> str:
        backend = await self._get_backend()
        uid = f"{uuid.uuid4()}@clinic-manager"
        await self._run(backend.add, build_ical(uid, event))
        return uid

    async def update_event(self, event_id: str, event: CalendarEvent) -> None:
        backend = await self._get_backend()
        replaced: bool = await self._run(backend.replace, event_id, build_ical(event_id, event))
        if not replaced:
            raise CalendarError("caldav event not found")

    async def delete_event(self, event_id: str) -> None:
        backend = await self._get_backend()
        await self._run(backend.delete, event_id)


# --------------------------------------------------------------------------- pure helpers (unit-tested)


def _to_datetime(value: Any, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=tz)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=tz)
    raise ValueError("unsupported date value")


def busy_from_components(components: Iterable[Any], tz: ZoneInfo) -> list[BusyInterval]:
    busy: list[BusyInterval] = []
    for comp in components:
        if str(comp.get("STATUS", "")).upper() == "CANCELLED" or str(comp.get("TRANSP", "")).upper() == "TRANSPARENT":
            continue
        try:
            start = _to_datetime(comp.decoded("DTSTART"), tz)
            if comp.get("DTEND") is not None:
                end = _to_datetime(comp.decoded("DTEND"), tz)
            elif comp.get("DURATION") is not None:
                end = start + comp.decoded("DURATION")
            else:  # RFC 5545: all-day without end lasts one day; timed without end is instantaneous
                is_all_day = not isinstance(comp.decoded("DTSTART"), datetime)
                end = start + (timedelta(days=1) if is_all_day else timedelta(0))
        except (KeyError, ValueError):
            continue
        if end <= start:
            continue
        busy.append(BusyInterval(start=start, end=end, event_id=str(comp.get("UID", "")) or None))
    return sorted(busy, key=lambda b: (b.start, b.end))


def build_ical(uid: str, event: CalendarEvent) -> str:
    from icalendar import Calendar, Event

    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    vevent = Event()
    vevent.add("uid", uid)
    vevent.add("summary", event.title)
    vevent.add("description", event.description)
    vevent.add("dtstart", event.start)
    vevent.add("dtend", event.end)
    vevent.add("dtstamp", datetime.now(UTC))
    vevent.add("transp", "OPAQUE")
    vevent.add(X_APPOINTMENT_ID, event.appointment_id)
    cal.add_component(vevent)
    return str(cal.to_ical().decode())
