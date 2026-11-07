"""Relative date tokens (ADR-0010): resolved in the *viewer's* time zone, at query time, never stored.

Tokens: today, tomorrow, yesterday, start_of_week (Monday), end_of_week, start_of_next_week, end_of_next_week,
start_of_month, end_of_month,
`+Nd`/`-Nd` (days), `+Nw`/`-Nw`, and ISO dates (`2026-10-23`).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_REL = re.compile(r"^([+-])(\d{1,4})([dw])$")
TOKENS = (
    "today", "tomorrow", "yesterday", "start_of_week", "end_of_week", "start_of_next_week", "end_of_next_week",
    "start_of_month", "end_of_month",
)  # fmt: skip


class DateTokenError(ValueError):
    pass


def today_in(tz: str, now: datetime) -> date:
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return (now if now.tzinfo else now.replace(tzinfo=ZoneInfo("UTC"))).astimezone(zone).date()


def is_token(text: str) -> bool:
    if text in TOKENS or _REL.match(text):
        return True
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def resolve(text: str, today: date) -> date:
    if text == "today":
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text == "yesterday":
        return today - timedelta(days=1)
    if text == "start_of_week":
        return today - timedelta(days=today.weekday())
    if text == "end_of_week":
        return today + timedelta(days=6 - today.weekday())
    if text == "start_of_next_week":
        return today - timedelta(days=today.weekday()) + timedelta(days=7)
    if text == "end_of_next_week":
        return today + timedelta(days=13 - today.weekday())
    if text == "start_of_month":
        return today.replace(day=1)
    if text == "end_of_month":
        nxt = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        return nxt - timedelta(days=1)
    if m := _REL.match(text):
        n = int(m.group(2)) * (7 if m.group(3) == "w" else 1)
        return today + timedelta(days=n if m.group(1) == "+" else -n)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DateTokenError(f"not a date or relative token: {text!r}") from exc
