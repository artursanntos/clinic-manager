"""Tiny in-memory fakes of the Google Calendar and Microsoft Graph HTTP APIs (for httpx.MockTransport)."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime
from typing import Any

import httpx


class FakeGoogleApi:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self._ids = itertools.count(1)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == "Bearer google-token"
        parts = request.url.path.split("/events")
        event_id = parts[1].strip("/") if len(parts) > 1 else ""
        if request.method == "GET":
            t_min = datetime.fromisoformat(request.url.params["timeMin"])
            t_max = datetime.fromisoformat(request.url.params["timeMax"])
            items = [
                e
                for e in self.events.values()
                if datetime.fromisoformat(e["start"]["dateTime"]) < t_max
                and t_min < datetime.fromisoformat(e["end"]["dateTime"])
            ]
            return httpx.Response(200, json={"items": items})
        if request.method == "POST":
            body = json.loads(request.content)
            new_id = f"g{next(self._ids)}"
            self.events[new_id] = {**body, "id": new_id, "status": "confirmed"}
            return httpx.Response(200, json=self.events[new_id])
        if request.method == "PATCH":
            if event_id not in self.events:
                return httpx.Response(404, json={})
            self.events[event_id].update(json.loads(request.content))
            return httpx.Response(200, json=self.events[event_id])
        if request.method == "DELETE":
            if self.events.pop(event_id, None) is None:
                return httpx.Response(410, json={})
            return httpx.Response(204)
        return httpx.Response(405)


class FakeGraphApi:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self._ids = itertools.count(1)

    @staticmethod
    def _dt(value: dict[str, str]) -> datetime:
        return datetime.fromisoformat(value["dateTime"]).replace(tzinfo=UTC)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/oauth2/v2.0/token"):
            return httpx.Response(200, json={"access_token": "graph-token", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer graph-token"
        if path.endswith("/calendarView"):
            start = datetime.fromisoformat(request.url.params["startDateTime"])
            end = datetime.fromisoformat(request.url.params["endDateTime"])
            items = [
                {**e, "start": {**e["start"], "dateTime": e["start"]["dateTime"] + ".0000000"}}
                for e in self.events.values()
                if self._dt(e["start"]) < end and start < self._dt(e["end"])
            ]
            return httpx.Response(200, json={"value": items})
        if request.method == "POST" and path.endswith("/events"):
            new_id = f"m{next(self._ids)}"
            self.events[new_id] = {**json.loads(request.content), "id": new_id, "isCancelled": False}
            return httpx.Response(201, json=self.events[new_id])
        event_id = path.rsplit("/", 1)[-1]
        if request.method == "PATCH":
            if event_id not in self.events:
                return httpx.Response(404, json={})
            self.events[event_id].update(json.loads(request.content))
            return httpx.Response(200, json=self.events[event_id])
        if request.method == "DELETE":
            if self.events.pop(event_id, None) is None:
                return httpx.Response(404, json={})
            return httpx.Response(204)
        return httpx.Response(405)
