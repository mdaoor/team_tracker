"""One protocol for every calendar (Phase 15 §15.3). Google and Microsoft Graph implement it; CalDAV can follow without touching callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

Provider = Literal["google", "microsoft"]
Response = Literal["accepted", "tentative", "declined", "organizer", "none"]
EventStatus = Literal["confirmed", "tentative", "cancelled"]
EventType = Literal["default", "outOfOffice", "focusTime", "workingLocation"]


class ProviderError(Exception):
    """A calendar call failed in a way the caller should show, never crash on."""


class AuthRequired(ProviderError):
    """No token, or the refresh token was revoked: the person must connect again."""


class RateLimited(ProviderError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"rate limited; retry after {retry_after:.0f} s")
        self.retry_after = retry_after


class SyncTokenExpired(ProviderError):
    """Google answered 410: the sync token is gone, do a full sync."""


@dataclass
class CalendarInfo:
    id: str
    name: str
    primary: bool = False
    selected: bool = True


@dataclass
class CalendarEvent:
    """The normalised event. `event_id` is unique per occurrence; `series_id` is shared by a recurring series."""

    provider: Provider
    calendar_id: str
    event_id: str
    title: str
    start: datetime
    end: datetime
    ical_uid: str | None = None
    series_id: str | None = None
    all_day: bool = False
    status: EventStatus = "confirmed"
    response: Response = "none"
    attendees: list[str] = field(default_factory=list)  # emails, lower-case, without me
    organizer: str | None = None
    location: str | None = None
    online_url: str | None = None  # Teams / Meet join link
    is_private: bool = False
    event_type: EventType = "default"
    categories: list[str] = field(default_factory=list)
    etag: str | None = None
    instance_start: datetime | None = None
    removed: bool = False  # the provider says this event no longer exists
    attended_min: int | None = None  # Teams attendance report, when permitted

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.provider, self.calendar_id, self.event_id)


@dataclass
class SyncPage:
    """Result of one (possibly multi-page) sync of one calendar. `cursor` is stored and passed back next time."""

    events: list[CalendarEvent]
    cursor: dict[str, Any]
    full: bool = False


class CalendarProvider(Protocol):
    name: Provider

    def list_calendars(self) -> list[CalendarInfo]: ...

    def sync(
        self,
        calendar_id: str,
        *,
        cursor: dict[str, Any] | None,
        window: tuple[datetime, datetime],
        me: str | None = None,
    ) -> SyncPage: ...

    def attended_minutes(self, event: CalendarEvent, me: str | None) -> int | None: ...
