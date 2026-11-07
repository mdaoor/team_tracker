"""Local dates for business rules (Phase 13 R3.5, review T8).

The server-local date is the wrong day for someone elsewhere: a Riyadh reporter at 01:00 and a New York manager are on different days. Business
dates therefore come from the workspace or person zone through these helpers (ruff DTZ011 bans the server-local call).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_today(now: datetime | None = None) -> date:
    return (now or utc_now()).astimezone(UTC).date()


def today_in(tz: str | None, now: datetime | None = None) -> date:
    """The calendar date it currently is in `tz` (falls back to UTC for an unknown zone)."""
    return (now or utc_now()).astimezone(_zone(tz)).date()


def ws_today(ws: Any, now: datetime | None = None) -> date:
    """Today in the workspace's zone (`settings.time_zone`, default UTC)."""
    return today_in((getattr(ws, "settings", None) or {}).get("time_zone"), now)


def person_today(person: Any, now: datetime | None = None) -> date:
    """Today where the person is."""
    return today_in(getattr(person, "time_zone", None), now)
