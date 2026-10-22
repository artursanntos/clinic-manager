"""Google Calendar via REST (httpx) + google-auth for tokens.

Auth: service account. The dentist shares her calendar with the service account's e-mail ("Make changes to
events"). On Cloud Run, leave ``GOOGLE_CREDENTIALS_FILE`` empty to use the runtime service account (ADC): no key
file to manage. Install with ``uv sync --extra google``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from app.adapters.calendar.base import APPOINTMENT_ID_KEY, CalendarError, CalendarEvent
from app.domain.models import BusyInterval

API = "https://www.googleapis.com/calendar/v3"
SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.readonly"]

TokenSource = Callable[[], Awaitable[str]]


class GoogleTokenSource:
    """Wraps google-auth credentials (sync refresh, run in a thread) and caches the token until it expires."""

    def __init__(self, credentials_file: str | None = None) -> None:
        import google.auth
        from google.oauth2 import service_account

        if credentials_file:
            self._credentials: Any = service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                credentials_file, scopes=SCOPES
            )
        else:
            self._credentials, _ = google.auth.default(scopes=SCOPES)
        self._lock = asyncio.Lock()

    async def __call__(self) -> str:
        async with self._lock:
            if not self._credentials.valid:
                from google.auth.transport.requests import Request

                await asyncio.to_thread(self._credentials.refresh, Request())
            return str(self._credentials.token)


class GoogleCalendar:
    def __init__(
        self,
        *,
        calendar_id: str,
        timezone: str = "America/Recife",
        credentials_file: str | None = None,
        token_source: TokenSource | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._tz = ZoneInfo(timezone)
        self._base = f"{API}/calendars/{quote(calendar_id, safe='')}/events"
        self._token = token_source or GoogleTokenSource(credentials_file)
        self._client = client or httpx.AsyncClient(timeout=15.0)

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            token = await self._token()
            response = await self._client.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        except httpx.HTTPError as exc:
            raise CalendarError(f"google calendar request failed: {type(exc).__name__}") from exc
        except Exception as exc:  # google-auth refresh errors
            raise CalendarError(f"google auth failed: {type(exc).__name__}") from exc
        return response

    @staticmethod
    def _check(response: httpx.Response, *, allow: tuple[int, ...] = ()) -> None:
        if response.status_code >= 400 and response.status_code not in allow:
            raise CalendarError(f"google calendar error: http {response.status_code}")

    async def list_busy(self, start: datetime, end: datetime) -> list[BusyInterval]:
        busy: list[BusyInterval] = []
        params: dict[str, Any] = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "singleEvents": "true",  # expands recurring events
            "orderBy": "startTime",
            "maxResults": 2500,
        }
        while True:
            response = await self._request("GET", self._base, params=params)
            self._check(response)
            data = response.json()
            for item in data.get("items", []):
                interval = parse_google_event(item, self._tz)
                if interval is not None:
                    busy.append(interval)
            token = data.get("nextPageToken")
            if not token:
                break
            params["pageToken"] = token
        return sorted(busy, key=lambda b: (b.start, b.end))

    async def create_event(self, event: CalendarEvent) -> str:
        response = await self._request("POST", self._base, json=google_event_body(event))
        self._check(response)
        return str(response.json()["id"])

    async def update_event(self, event_id: str, event: CalendarEvent) -> None:
        response = await self._request(
            "PATCH", f"{self._base}/{quote(event_id, safe='')}", json=google_event_body(event)
        )
        self._check(response)

    async def delete_event(self, event_id: str) -> None:
        response = await self._request("DELETE", f"{self._base}/{quote(event_id, safe='')}")
        self._check(response, allow=(404, 410))


def google_event_body(event: CalendarEvent) -> dict[str, Any]:
    return {
        "summary": event.title,
        "description": event.description,
        "start": {"dateTime": event.start.isoformat()},
        "end": {"dateTime": event.end.isoformat()},
        "extendedProperties": {"private": {APPOINTMENT_ID_KEY: event.appointment_id}},
    }


def _parse_when(value: dict[str, Any], tz: ZoneInfo) -> datetime | None:
    if "dateTime" in value:
        return datetime.fromisoformat(value["dateTime"])
    if "date" in value:  # all-day event (opaque): blocks the whole local day
        return datetime.combine(date.fromisoformat(value["date"]), time.min, tzinfo=tz)
    return None


def parse_google_event(item: dict[str, Any], tz: ZoneInfo) -> BusyInterval | None:
    """Busy interval for an event, or None if it doesn't block time (cancelled or marked as 'free')."""
    if item.get("status") == "cancelled" or item.get("transparency") == "transparent":
        return None
    start, end = _parse_when(item.get("start", {}), tz), _parse_when(item.get("end", {}), tz)
    if start is None or end is None or end <= start:
        return None
    return BusyInterval(start=start, end=end, event_id=str(item.get("id")))
