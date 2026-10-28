"""Microsoft 365 / Outlook / Teams adapter over Microsoft Graph (Phase 15 §15.3).

Authorization is OAuth 2.0 code + PKCE against the Microsoft identity platform (v2.0 endpoints), implemented in `oauth.py`, so MSAL is not
required; work or school and personal Microsoft accounts both work with tenant `common`. Scopes: `Calendars.Read` (+ `offline_access`);
`OnlineMeetings.Read` is optional and needs admin consent in most tenants (only used for attended minutes).

Sync uses `calendarView/delta` for a bounded window: first call returns every page and finally an `@odata.deltaLink`, which is stored
and called next time to get only changes (`@removed` entries for deletions). Teams meetings are ordinary events with `isOnlineMeeting` and
`onlineMeeting.joinUrl`, which become `CalendarEvent.online_url`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from tt_client.integrations.base import (
    CalendarEvent,
    CalendarInfo,
    EventType,
    ProviderError,
    Response,
    SyncPage,
)
from tt_client.integrations.http import ApiClient
from tt_client.integrations.oauth import Authorizer, OAuthConfig, TokenVault
from tt_client.secrets import SecretStore

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ("offline_access", "Calendars.Read")
OPTIONAL_ATTENDANCE_SCOPE = "OnlineMeetings.Read"
_RESPONSES: dict[str, Response] = {
    "accepted": "accepted",
    "tentativelyAccepted": "tentative",
    "declined": "declined",
    "organizer": "organizer",
    "none": "none",
    "notResponded": "none",
}


def oauth_config(
    client_id: str,
    *,
    tenant: str = "common",
    attendance: bool = False,
    redirect_uri: str = "http://127.0.0.1:8765/callback",
) -> OAuthConfig:
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    return OAuthConfig(
        client_id=client_id,
        auth_url=f"{base}/authorize",
        token_url=f"{base}/token",
        scopes=(*SCOPES, *((OPTIONAL_ATTENDANCE_SCOPE,) if attendance else ())),
        redirect_uri=redirect_uri,
        extra_auth={"response_mode": "query", "prompt": "select_account"},
    )


def _fmt(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _dt(node: dict[str, Any]) -> datetime:
    """Graph returns `dateTime` without an offset plus a `timeZone`; we ask for UTC with `Prefer: outlook.timezone="UTC"`."""
    raw = str(node["dateTime"])
    parsed = datetime.fromisoformat(
        raw[:26] if "." in raw else raw
    )  # 7-digit fractions are not valid for fromisoformat
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def normalise(raw: dict[str, Any], calendar_id: str, me: str | None = None) -> CalendarEvent:
    event_id = str(raw["id"])
    if "@removed" in raw:
        now = datetime.now(UTC)
        return CalendarEvent(
            "microsoft", calendar_id, event_id, "", now, now, removed=True, status="cancelled"
        )
    me = (me or "").lower()
    attendees = [
        str(a["emailAddress"]["address"]).lower()
        for a in raw.get("attendees", [])
        if a.get("emailAddress", {}).get("address")
        and str(a["emailAddress"]["address"]).lower() != me
        and a.get("type") != "resource"
    ]
    online = (raw.get("onlineMeeting") or {}).get("joinUrl") or (
        raw.get("onlineMeetingUrl") if raw.get("isOnlineMeeting") else None
    )
    show_as = raw.get("showAs")
    subject = str(raw.get("subject") or "(no title)")
    categories = list(raw.get("categories", []))
    event_type: EventType = "default"
    if show_as == "oof":
        event_type = "outOfOffice"
    elif any(c.lower().startswith("focus") for c in categories) or subject.lower().startswith("focus time"):
        event_type = "focusTime"
    start, end = _dt(raw["start"]), _dt(raw["end"])
    kind = raw.get("type", "singleInstance")
    return CalendarEvent(
        provider="microsoft",
        calendar_id=calendar_id,
        event_id=event_id,
        ical_uid=raw.get("iCalUId"),
        series_id=raw.get("seriesMasterId") if kind in ("occurrence", "exception") else None,
        title=subject,
        start=start,
        end=end,
        all_day=bool(raw.get("isAllDay")),
        status="cancelled"
        if raw.get("isCancelled")
        else "tentative"
        if show_as == "tentative"
        else "confirmed",
        response=_RESPONSES.get(str((raw.get("responseStatus") or {}).get("response", "none")), "none"),
        attendees=attendees,
        organizer=str((raw.get("organizer") or {}).get("emailAddress", {}).get("address", "")).lower()
        or None,
        location=(raw.get("location") or {}).get("displayName") or None,
        online_url=online,
        is_private=raw.get("sensitivity") in ("private", "personal", "confidential"),
        event_type=event_type,
        categories=categories,
        etag=raw.get("@odata.etag"),
        instance_start=start,
        removed=bool(raw.get("isCancelled")),
    )


class MicrosoftCalendar:
    name = "microsoft"

    def __init__(self, http: httpx.Client, secrets: SecretStore, cfg: OAuthConfig, clock: Any = None) -> None:
        import time as _t

        self.cfg = cfg
        self.vault = TokenVault(secrets, "microsoft")
        self.auth = Authorizer(http, cfg, self.vault, clock or _t.time)
        self.api = ApiClient(http, self.auth)
        self.attendance_enabled = OPTIONAL_ATTENDANCE_SCOPE in cfg.scopes

    def list_calendars(self) -> list[CalendarInfo]:
        out: list[CalendarInfo] = []
        url: str | None = f"{GRAPH}/me/calendars"
        while url:
            body = self.api.get(url)
            for c in body.get("value", []):
                out.append(
                    CalendarInfo(
                        id=c["id"], name=c.get("name", c["id"]), primary=bool(c.get("isDefaultCalendar"))
                    )
                )
            url = body.get("@odata.nextLink")
        return out

    def sync(
        self,
        calendar_id: str,
        *,
        cursor: dict[str, Any] | None,
        window: tuple[datetime, datetime],
        me: str | None = None,
    ) -> SyncPage:
        link = (cursor or {}).get("delta_link")
        headers = {"Prefer": 'outlook.timezone="UTC", odata.maxpagesize=100'}
        full = link is None
        if link is None:
            url: str | None = f"{GRAPH}/me/calendars/{quote(calendar_id, safe='')}/calendarView/delta"
            params: dict[str, Any] | None = {"startDateTime": _fmt(window[0]), "endDateTime": _fmt(window[1])}
        else:
            url, params = link, None  # the delta link already carries its token
        events: list[CalendarEvent] = []
        delta: str | None = None
        pages = 0
        while url:
            body = self.api.get(url, params, headers)
            params = None  # nextLink / deltaLink are complete URLs
            events += [normalise(e, calendar_id, me) for e in body.get("value", [])]
            url = body.get("@odata.nextLink")
            delta = body.get("@odata.deltaLink", delta)
            pages += 1
            if pages > 200:
                raise ProviderError("calendar delta did not finish after 200 pages")
        return SyncPage(events, {"delta_link": delta}, full)

    def attended_minutes(self, event: CalendarEvent, me: str | None) -> int | None:
        """Minutes `me` was in the Teams meeting, from the attendance report. None when not permitted or not found (admin consent)."""
        if not (self.attendance_enabled and event.online_url and me):
            return None
        try:
            found = self.api.get(
                f"{GRAPH}/me/onlineMeetings", {"$filter": f"JoinWebUrl eq '{event.online_url}'"}
            ).get("value", [])
            if not found:
                return None
            reports = self.api.get(f"{GRAPH}/me/onlineMeetings/{found[0]['id']}/attendanceReports").get(
                "value", []
            )
            if not reports:
                return None
            rep = self.api.get(
                f"{GRAPH}/me/onlineMeetings/{found[0]['id']}/attendanceReports/{reports[0]['id']}",
                {"$expand": "attendanceRecords"},
            )
        except ProviderError:
            return None
        for rec in rep.get("attendanceRecords", []):
            if str(rec.get("emailAddress", "")).lower() == me.lower():
                return round(int(rec.get("totalAttendanceInSeconds", 0)) / 60)
        return None
