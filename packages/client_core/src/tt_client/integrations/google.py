"""Google Calendar adapter (Phase 15 §15.3): `calendarList`, incremental sync with `syncToken`, normalisation to `CalendarEvent`.

Scopes: `calendar.readonly` (and `calendar.events` only when "write focus blocks back" is switched on, which this adapter does not do yet).
Poll-based: every 5 minutes while running, on wake and on demand (no push channel, so no public endpoint is needed).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import httpx

from tt_client.integrations.base import (
    CalendarEvent,
    CalendarInfo,
    EventType,
    Response,
    SyncPage,
    SyncTokenExpired,
)
from tt_client.integrations.http import ApiClient
from tt_client.integrations.oauth import Authorizer, OAuthConfig, TokenVault
from tt_client.secrets import SecretStore

API = "https://www.googleapis.com/calendar/v3"
SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)


def oauth_config(
    client_id: str, client_secret: str | None = None, redirect_uri: str = "http://127.0.0.1:8765/callback"
) -> OAuthConfig:
    return OAuthConfig(
        client_id=client_id,
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",  # noqa: S106 - an endpoint, not a secret
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        client_secret=client_secret,
        extra_auth={"access_type": "offline", "prompt": "consent"},
    )


def _when(node: dict[str, Any]) -> tuple[datetime, bool]:
    if "dateTime" in node:
        return datetime.fromisoformat(str(node["dateTime"]).replace("Z", "+00:00")), False
    d = date.fromisoformat(node["date"])
    return datetime.combine(d, time(), tzinfo=UTC), True


def normalise(raw: dict[str, Any], calendar_id: str, me: str | None = None) -> CalendarEvent:
    if (
        "start" not in raw
    ):  # a deleted/cancelled instance in an incremental answer carries only id, etag and status
        now = datetime.now(UTC)
        return CalendarEvent(
            "google",
            calendar_id,
            str(raw["id"]),
            "",
            now,
            now,
            status="cancelled",
            etag=raw.get("etag"),
            removed=True,
        )
    start, all_day = _when(raw.get("start", {}))
    end, _ = _when(raw.get("end", raw.get("start", {})))
    me = (me or "").lower()
    response: Response = "none"
    attendees: list[str] = []
    for a in raw.get("attendees", []):
        email = str(a.get("email", "")).lower()
        if a.get("self") or (me and email == me):
            response = {"accepted": "accepted", "declined": "declined", "tentative": "tentative"}.get(
                str(a.get("responseStatus")), "none"
            )  # type: ignore[assignment]
        elif email and not a.get("resource"):
            attendees.append(email)
    organizer = raw.get("organizer", {})
    if organizer.get("self") or (me and str(organizer.get("email", "")).lower() == me):
        response = "organizer"
    online = raw.get("hangoutLink")
    for ep in (raw.get("conferenceData") or {}).get("entryPoints", []):
        if ep.get("entryPointType") == "video" and ep.get("uri"):
            online = ep["uri"]
            break
    orig = raw.get("originalStartTime")
    instance_start = _when(orig)[0] if orig else start
    event_type: EventType = raw.get("eventType", "default")
    if event_type not in ("default", "outOfOffice", "focusTime", "workingLocation"):
        event_type = "default"
    return CalendarEvent(
        provider="google",
        calendar_id=calendar_id,
        event_id=str(raw["id"]),
        ical_uid=raw.get("iCalUID"),
        series_id=raw.get("recurringEventId"),
        title=str(raw.get("summary") or "(no title)"),
        start=start,
        end=end,
        all_day=all_day,
        status=raw.get("status", "confirmed"),
        response=response,
        attendees=attendees,
        organizer=str(organizer.get("email", "")).lower() or None,
        location=raw.get("location"),
        online_url=online,
        is_private=raw.get("visibility") in ("private", "confidential"),
        event_type=event_type,
        etag=raw.get("etag"),
        instance_start=instance_start,
        removed=raw.get("status") == "cancelled",
    )


class GoogleCalendar:
    name = "google"

    def __init__(self, http: httpx.Client, secrets: SecretStore, cfg: OAuthConfig, clock: Any = None) -> None:
        import time as _t

        self.cfg = cfg
        self.vault = TokenVault(secrets, "google")
        self.auth = Authorizer(http, cfg, self.vault, clock or _t.time)
        self.api = ApiClient(http, self.auth)

    def list_calendars(self) -> list[CalendarInfo]:
        out: list[CalendarInfo] = []
        token: str | None = None
        while True:
            body = self.api.get(
                f"{API}/users/me/calendarList", {"maxResults": 250, **({"pageToken": token} if token else {})}
            )
            for c in body.get("items", []):
                out.append(
                    CalendarInfo(
                        id=c["id"],
                        name=c.get("summaryOverride") or c.get("summary", c["id"]),
                        primary=bool(c.get("primary")),
                        selected=bool(c.get("selected", True)) and c.get("accessRole") != "freeBusyReader",
                    )
                )
            token = body.get("nextPageToken")
            if not token:
                return out

    def sync(
        self,
        calendar_id: str,
        *,
        cursor: dict[str, Any] | None,
        window: tuple[datetime, datetime],
        me: str | None = None,
    ) -> SyncPage:
        """Incremental when `cursor` has a `syncToken`; a 410 falls back to a full window sync."""
        token = (cursor or {}).get("sync_token")
        if token:
            try:
                return self._pull(calendar_id, {"syncToken": token}, me, full=False)
            except SyncTokenExpired:
                pass  # tokens expire server-side (410): start again from the window
        params = {
            "timeMin": window[0].astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "timeMax": window[1].astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        return self._pull(calendar_id, params, me, full=True)

    def _pull(self, calendar_id: str, params: dict[str, str], me: str | None, *, full: bool) -> SyncPage:
        events: list[CalendarEvent] = []
        page: str | None = None
        while True:
            q: dict[str, Any] = {"singleEvents": "true", "maxResults": 250, "showDeleted": "true", **params}
            if page:
                q["pageToken"] = page
            body = self.api.get(f"{API}/calendars/{_q(calendar_id)}/events", q)
            events += [normalise(e, calendar_id, me) for e in body.get("items", [])]
            page = body.get("nextPageToken")
            if not page:
                return SyncPage(events, {"sync_token": body.get("nextSyncToken")}, full)

    def attended_minutes(self, event: CalendarEvent, me: str | None) -> int | None:
        return None  # Google Meet attendance needs Workspace reports; not part of v1


def _q(calendar_id: str) -> str:
    from urllib.parse import quote

    return quote(calendar_id, safe="")
