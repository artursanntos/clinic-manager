"""Microsoft 365 / Outlook.com calendar via Microsoft Graph (httpx only, no SDK).

Two auth modes:
- ``refresh_token`` (personal Outlook.com/Hotmail, or any account with delegated consent): run ``clinic ms-login``
  once to get ``MS_REFRESH_TOKEN``; requests go to ``/me``.
- ``client_credentials`` (Microsoft 365 work account, admin consent for ``Calendars.ReadWrite`` application
  permission): requests go to ``/users/{MS_USER_ID}``.
"""

from __future__ import annotations

import asyncio
import time as _time
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from app.adapters.calendar.base import APPOINTMENT_ID_KEY, CalendarError, CalendarEvent
from app.domain.models import BusyInterval

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
DELEGATED_SCOPE = "offline_access https://graph.microsoft.com/Calendars.ReadWrite"
APP_SCOPE = "https://graph.microsoft.com/.default"
# Private named extended property linking events to appointments (GUID is arbitrary but fixed for this app).
EXTENDED_PROPERTY_ID = f"String {{6c3f3d1e-4a0f-4a8e-9a57-2f1a3c0b9d11}} Name {APPOINTMENT_ID_KEY}"


class MicrosoftTokenProvider:
    def __init__(
        self,
        *,
        mode: Literal["client_credentials", "refresh_token"],
        tenant_id: str,
        client_id: str,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if mode == "client_credentials" and not client_secret:
            raise ValueError("client_credentials mode needs MS_CLIENT_SECRET")
        if mode == "refresh_token" and not refresh_token:
            raise ValueError("refresh_token mode needs MS_REFRESH_TOKEN (run `clinic ms-login`)")
        self.mode = mode
        self._url = f"{LOGIN}/{tenant_id}/oauth2/v2.0/token"
        self._client_id, self._secret, self._refresh = client_id, client_secret, refresh_token
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def __call__(self) -> str:
        async with self._lock:
            if self._token and _time.monotonic() < self._expires_at - 60:
                return self._token
            data: dict[str, str] = {"client_id": self._client_id}
            if self.mode == "client_credentials":
                data |= {"grant_type": "client_credentials", "scope": APP_SCOPE, "client_secret": self._secret or ""}
            else:
                data |= {"grant_type": "refresh_token", "scope": DELEGATED_SCOPE, "refresh_token": self._refresh or ""}
                if self._secret:
                    data["client_secret"] = self._secret
            try:
                response = await self._client.post(self._url, data=data)
            except httpx.HTTPError as exc:
                raise CalendarError(f"microsoft token request failed: {type(exc).__name__}") from exc
            if response.status_code >= 400:
                raise CalendarError(f"microsoft token error: http {response.status_code}")
            payload = response.json()
            self._token = str(payload["access_token"])
            self._expires_at = _time.monotonic() + float(payload.get("expires_in", 3600))
            # Microsoft may rotate refresh tokens; keep the newest for this process.
            self._refresh = payload.get("refresh_token", self._refresh)
            return self._token


class MicrosoftCalendar:
    def __init__(
        self,
        tokens: MicrosoftTokenProvider | Any,
        *,
        user_id: str | None = None,
        calendar_id: str | None = None,
        timezone: str = "America/Recife",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        owner = f"{GRAPH}/users/{user_id}" if user_id else f"{GRAPH}/me"
        self._calendar = f"{owner}/calendars/{calendar_id}" if calendar_id else f"{owner}/calendar"
        self._events = f"{owner}/events"
        self._tokens = tokens
        self._tz = ZoneInfo(timezone)
        self._client = client or httpx.AsyncClient(timeout=15.0)

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        token = await self._tokens()
        headers = {"Authorization": f"Bearer {token}", "Prefer": 'outlook.timezone="UTC"'}
        try:
            return await self._client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise CalendarError(f"graph request failed: {type(exc).__name__}") from exc

    @staticmethod
    def _check(response: httpx.Response, *, allow: tuple[int, ...] = ()) -> None:
        if response.status_code >= 400 and response.status_code not in allow:
            raise CalendarError(f"graph error: http {response.status_code}")

    async def list_busy(self, start: datetime, end: datetime) -> list[BusyInterval]:
        busy: list[BusyInterval] = []
        url: str | None = f"{self._calendar}/calendarView"
        params: dict[str, Any] | None = {
            "startDateTime": start.astimezone(UTC).isoformat(),
            "endDateTime": end.astimezone(UTC).isoformat(),
            "$select": "id,start,end,showAs,isCancelled,isAllDay",
            "$top": 500,
        }
        while url:
            response = await self._request("GET", url, params=params)
            self._check(response)
            data = response.json()
            for item in data.get("value", []):
                interval = parse_graph_event(item, self._tz)
                if interval is not None:
                    busy.append(interval)
            url, params = data.get("@odata.nextLink"), None  # nextLink already carries the query
        return sorted(busy, key=lambda b: (b.start, b.end))

    async def create_event(self, event: CalendarEvent) -> str:
        response = await self._request("POST", f"{self._calendar}/events", json=graph_event_body(event))
        self._check(response)
        return str(response.json()["id"])

    async def update_event(self, event_id: str, event: CalendarEvent) -> None:
        response = await self._request("PATCH", f"{self._events}/{event_id}", json=graph_event_body(event))
        self._check(response)

    async def delete_event(self, event_id: str) -> None:
        response = await self._request("DELETE", f"{self._events}/{event_id}")
        self._check(response, allow=(404,))


def graph_event_body(event: CalendarEvent) -> dict[str, Any]:
    def when(dt: datetime) -> dict[str, str]:
        return {"dateTime": dt.astimezone(UTC).replace(tzinfo=None).isoformat(), "timeZone": "UTC"}

    return {
        "subject": event.title,
        "body": {"contentType": "text", "content": event.description},
        "start": when(event.start),
        "end": when(event.end),
        "showAs": "busy",
        "singleValueExtendedProperties": [{"id": EXTENDED_PROPERTY_ID, "value": event.appointment_id}],
    }


def _parse_graph_time(value: dict[str, Any], tz: ZoneInfo, all_day: bool) -> datetime:
    raw = str(value["dateTime"])
    # Graph returns up to 7 fractional digits; Python accepts at most 6.
    if "." in raw:
        head, frac = raw.split(".", 1)
        raw = f"{head}.{frac[:6]}"
    naive = datetime.fromisoformat(raw)
    if all_day:
        return datetime.combine(date(naive.year, naive.month, naive.day), time.min, tzinfo=tz)
    zone = UTC if value.get("timeZone", "UTC") == "UTC" else ZoneInfo(value["timeZone"])
    return naive.replace(tzinfo=zone) if naive.tzinfo is None else naive


def parse_graph_event(item: dict[str, Any], tz: ZoneInfo) -> BusyInterval | None:
    if item.get("isCancelled") or item.get("showAs") == "free":
        return None
    all_day = bool(item.get("isAllDay"))
    try:
        start = _parse_graph_time(item["start"], tz, all_day)
        end = _parse_graph_time(item["end"], tz, all_day)
    except (KeyError, ValueError):
        return None
    if end <= start:
        return None
    return BusyInterval(start=start, end=end, event_id=str(item.get("id")))


async def device_code_login(*, client_id: str, tenant_id: str = "consumers") -> str:
    """Interactive one-time login (prints a code to enter at microsoft.com/devicelogin). Returns a refresh token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        start = await client.post(
            f"{LOGIN}/{tenant_id}/oauth2/v2.0/devicecode", data={"client_id": client_id, "scope": DELEGATED_SCOPE}
        )
        start.raise_for_status()
        flow = start.json()
        print(flow["message"])
        interval = float(flow.get("interval", 5))
        deadline = _time.monotonic() + float(flow.get("expires_in", 900))
        while _time.monotonic() < deadline:
            await asyncio.sleep(interval)
            response = await client.post(
                f"{LOGIN}/{tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": flow["device_code"],
                },
            )
            payload = response.json()
            if response.status_code == 200:
                return str(payload["refresh_token"])
            if payload.get("error") not in ("authorization_pending", "slow_down"):
                raise CalendarError(f"device login failed: {payload.get('error')}")
            if payload.get("error") == "slow_down":
                interval += 5
    raise CalendarError("device login timed out")
