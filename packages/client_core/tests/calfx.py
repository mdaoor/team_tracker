"""Test doubles for the calendar providers: fixtures from tests/fixtures/calendar served through httpx.MockTransport. No real network."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

FIX = Path(__file__).parent / "fixtures" / "calendar"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIX / name).read_text())  # type: ignore[no-any-return]


class Calls:
    """Records every request so a test can assert what was (not) asked for, and which headers were sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def client(handler: Callable[[httpx.Request], httpx.Response], calls: Calls | None = None) -> httpx.Client:
    def wrapped(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.requests.append(req)
        return handler(req)

    return httpx.Client(transport=httpx.MockTransport(wrapped))


def json_response(
    body: dict[str, Any], status: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


def google_handler(state: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    """Serves calendarList + events. `state['incremental']` switches to the SYNC-1 -> events_incremental answer; `state['gone']` answers 410 once."""

    def handler(req: httpx.Request) -> httpx.Response:
        url = urlparse(str(req.url))
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/token" or str(req.url).startswith("https://oauth2.googleapis.com/token"):
            state["token_calls"] = state.get("token_calls", 0) + 1
            return json_response(load("google/token_refreshed.json"))
        if (
            req.headers.get("Authorization")
            != f"Bearer {state.get('valid_token', 'ya29.GOOGLE-ACCESS-TOKEN-1')}"
        ):
            return json_response({"error": {"code": 401, "message": "Invalid Credentials"}}, 401)
        if url.path.endswith("/users/me/calendarList"):
            return json_response(load("google/calendar_list.json"))
        if "/calendars/" in url.path and url.path.endswith("/events"):
            cal = unquote(url.path.split("/calendars/")[1].rsplit("/events", 1)[0])
            if state.get("rate_limit"):
                return json_response({"error": {"message": "rateLimitExceeded"}}, 429, {"Retry-After": "120"})
            if "syncToken" in q:
                if state.pop("gone", False) or q["syncToken"] not in ("SYNC-1", "SYNC-2"):
                    return json_response(load("google/error_410.json"), 410)
                return json_response(load("google/events_incremental.json"))
            if cal != "alice@acme.com" and cal != "mahmoud@acme.com":
                return json_response({"items": [], "nextSyncToken": f"EMPTY-{cal}"})
            if q.get("pageToken") == "PAGE2":
                return json_response(load("google/events_page2.json"))
            return json_response(load("google/events_page1.json"))
        return json_response({"error": {"message": f"unexpected {req.url}"}}, 404)

    return handler


def microsoft_handler(state: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "login.microsoftonline.com" in url and url.endswith("/token"):
            state["token_calls"] = state.get("token_calls", 0) + 1
            return json_response(load("microsoft/token_refreshed.json"))
        if req.headers.get("Authorization") != f"Bearer {state.get('valid_token', 'EwB-MS-ACCESS-TOKEN-1')}":
            return json_response({"error": {"code": "InvalidAuthenticationToken", "message": "expired"}}, 401)
        p = urlparse(url)
        if p.path.endswith("/me/calendars"):
            return json_response(load("microsoft/calendars.json"))
        if p.path.endswith("/calendarView/delta"):
            if "AAMkAD-cal-2" in p.path:
                return json_response(
                    {
                        "value": [],
                        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/calendars/AAMkAD-cal-2/calendarView/delta?$deltatoken=EMPTY",
                    }
                )
            if "deltatoken" in p.query:
                if state.pop("delta_gone", False):  # Graph: the delta token aged out
                    return json_response(
                        {
                            "error": {
                                "code": "syncStateNotFound",
                                "message": "The sync state generation is not found.",
                            }
                        },
                        410,
                    )
                return json_response(load("microsoft/delta_incremental.json"))
            if "skiptoken" in p.query:
                return json_response(load("microsoft/delta_page2.json"))
            if "AAMkAD-cal-2" in p.path:
                return json_response(
                    {
                        "value": [],
                        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/calendars/AAMkAD-cal-2/calendarView/delta?$deltatoken=EMPTY",
                    }
                )
            return json_response(load("microsoft/delta_page1.json"))
        if p.path.endswith("/me/onlineMeetings"):
            if state.get("no_consent"):
                return json_response(
                    {"error": {"code": "Forbidden", "message": "admin consent required"}}, 403
                )
            return json_response(load("microsoft/online_meetings.json"))
        if p.path.endswith("/attendanceReports"):
            return json_response(load("microsoft/attendance_reports.json"))
        if "/attendanceReports/" in p.path:
            return json_response(load("microsoft/attendance_report.json"))
        return json_response({"error": {"message": f"unexpected {url}"}}, 404)

    return handler
