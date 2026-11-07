"""Durations and dates for the deterministic parser (numbers and dates are where small models err)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import dateparser

_NUM = r"\d+(?:\.\d+)?"
_UNIT = r"(?:days?|d|hours?|hrs?|h|minutes?|mins?|m)"
_DUR_PART = rf"(?P<n>{_NUM})\s*(?P<u>{_UNIT})(?![a-z])"
_DUR_FULL = re.compile(rf"^(?:{_DUR_PART}(?:\s*(?:and|,)?\s*)?)+$", re.I)
_DUR_ITER = re.compile(_DUR_PART, re.I)
_WORDS = {"an hour": 60, "half an hour": 30, "half hour": 30, "a day": None}

_HAS_TIME = re.compile(
    r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|\b\d{1,2}:\d{2}\b|\bnoon\b|\bmidnight\b|\beod\b", re.I
)
_EOD = re.compile(r"^(?:eod|end of (?:the )?day|today eod|cob)$", re.I)
_EOW = re.compile(r"^(?:eow|end of (?:the )?week)$", re.I)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def parse_duration(text: str, hours_per_day: int = 8) -> int | None:
    """'2h', '45 min', '1h30m', '1.5h', '2d' (= hours_per_day hours) -> minutes; None if not a duration."""
    t = text.strip().lower().rstrip(".")
    t = re.sub(r"^(?:about|around|roughly|approx\.?|for)\s+", "", t)
    if t in _WORDS and _WORDS[t] is not None:
        return _WORDS[t]
    if not _DUR_FULL.match(t):
        return None
    total = 0.0
    for mt in _DUR_ITER.finditer(t):
        n, u = float(mt.group("n")), mt.group("u")[0]
        total += n * (hours_per_day * 60 if u == "d" else 60 if u == "h" else 1)
    minutes = round(total)
    return minutes if minutes > 0 else None


def parse_when(text: str, now: datetime, tz: str) -> datetime | None:
    """'tomorrow 5pm', 'friday', 'eod', 'next week', ISO -> aware datetime in `tz` (17:00 if no time)."""
    t = text.strip().strip(".")
    if not t:
        return None
    zi = zone(tz)
    local = now.astimezone(zi)
    if _EOD.match(t):
        return local.replace(hour=17, minute=0, second=0, microsecond=0)
    if _EOW.match(t):
        days = (4 - local.weekday()) % 7
        return (local + timedelta(days=days)).replace(hour=17, minute=0, second=0, microsecond=0)
    parsed = dateparser.parse(
        t,
        languages=["en"],
        settings={
            "TIMEZONE": zi.key,
            "TO_TIMEZONE": zi.key,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": local.replace(tzinfo=None),
        },
    )
    if parsed is None:
        return None
    if not _HAS_TIME.search(t):
        parsed = parsed.replace(hour=17, minute=0, second=0, microsecond=0)
    return parsed.astimezone(zi)
