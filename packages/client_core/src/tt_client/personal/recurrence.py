"""RRULE materialisation for personal items (Phase 15 §15.1) across time zones and DST.

A series stores its first occurrence (`start`, aware) and `tz`. The rule is expanded on the *local wall clock* (naive datetimes in `tz`),
so "every weekday 7:00" stays 7:00 after the clocks change. Each local occurrence is then resolved to an instant:

* a wall time that does not exist (spring-forward gap) moves forward by the gap, so it still happens that day;
* an ambiguous wall time (autumn overlap) means the first occurrence;
* the elapsed length of an instance is constant (`duration_min`), whatever the offset change in between.

Instances are not stored. A stored row with `series_id == master.id` and an `instance_date` *overrides* the generated occurrence of that
date (completed, skipped, moved). Skipping or completing an instance never touches the server.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

from tt_client.personal.model import PersonalItem, Status

MAX_INSTANCES = 2000
_UNTIL = re.compile(r"(UNTIL=)(\d{8})(?:T(\d{6}))?Z?", re.I)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def normalise_rrule(rule: str) -> str:
    """Drop `RRULE:`, upper-case, and turn `UNTIL=...Z` into a naive local value so it compares with the naive wall-clock expansion."""
    body = rule.strip()
    if body.upper().startswith("RRULE:"):
        body = body[6:]
    body = body.upper()
    return _UNTIL.sub(lambda m: f"UNTIL={m.group(2)}T{m.group(3) or '235959'}", body)


def validate_rrule(rule: str, tz: str = "UTC") -> None:
    """Raises ValueError for a rule dateutil cannot expand."""
    rrulestr(normalise_rrule(rule), dtstart=datetime(2026, 1, 1, 9, 0))  # noqa: DTZ001 - naive wall clock by design


def resolve_local(naive: datetime, tz: ZoneInfo) -> datetime:
    """A local wall time -> an instant (UTC). Gap: shifted forward by the gap. Overlap: the first occurrence.

    Everything the tracker stores or compares is UTC: Python compares and subtracts two datetimes that share one `ZoneInfo` by their
    wall clocks, which is wrong across a DST change. Local time is only for display (`astimezone(zone(tz))`).
    """
    return naive.replace(tzinfo=tz, fold=0).astimezone(UTC)


def _local_naive(moment: datetime, tz: ZoneInfo) -> datetime:
    return moment.astimezone(tz).replace(tzinfo=None)


def occurrence_dates(master: PersonalItem, first: datetime, last: datetime) -> list[datetime]:
    """Naive local occurrence starts of `master` within [first, last] (naive local bounds)."""
    assert master.start is not None and master.rrule  # noqa: S101
    tz = zone(master.tz)
    rule = rrulestr(normalise_rrule(master.rrule), dtstart=_local_naive(master.start, tz))
    return [d for d in rule.between(first, last, inc=True)][:MAX_INSTANCES]


def materialise(
    master: PersonalItem,
    window_start: datetime,
    window_end: datetime,
    overrides: Iterable[PersonalItem] | Mapping[date, PersonalItem] = (),
) -> list[PersonalItem]:
    """Instances of a recurring `master` that start within [window_start, window_end), ordered by start.

    `overrides` are stored rows of the series keyed by their `instance_date`; they replace the generated occurrence.
    Cancelled overrides drop the occurrence; skipped ones stay visible (status skipped) so the UI can show them struck through.
    """
    if not master.is_recurring or master.start is None:
        return []
    tz = zone(master.tz)
    by_date: dict[date, PersonalItem] = (
        dict(overrides)
        if isinstance(overrides, Mapping)
        else {o.instance_date: o for o in overrides if o.instance_date}
    )
    duration = timedelta(minutes=master.duration_min or 0)
    # widen by a day each side so a zone offset never hides an instance; exact filtering happens on the instant below
    first = _local_naive(window_start, tz) - timedelta(days=1)
    last = _local_naive(window_end, tz) + timedelta(days=1)
    out: list[PersonalItem] = []
    for naive in occurrence_dates(master, first, last):
        start = resolve_local(naive, tz)
        if not (window_start <= start < window_end):
            continue
        day = naive.date()
        over = by_date.get(day)
        if over is not None:
            if over.deleted_at is not None or over.status == Status.CANCELLED:
                continue
            out.append(over)
            continue
        out.append(
            master.model_copy(
                update={
                    "id": f"{master.id}@{day.isoformat()}",
                    "series_id": master.id,
                    "instance_date": day,
                    "rrule": None,
                    "start": start,
                    "end": start + duration if master.duration_min is not None else None,
                    "status": Status.PLANNED,
                    "sent_refs": [],
                    "calendar_ref": None,
                }
            )
        )
    out.sort(key=lambda i: (i.start or window_start, i.id))
    return out


def instance_id(master_id: str, day: date) -> str:
    return f"{master_id}@{day.isoformat()}"


def split_instance_id(item_id: str) -> tuple[str, date] | None:
    if "@" not in item_id:
        return None
    master, _, day = item_id.rpartition("@")
    try:
        return master, date.fromisoformat(day)
    except ValueError:
        return None


# ------------------------------------------------------------------------------ rule text (the recurring editor preview)
_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_NAMES = {
    "MO": "Monday",
    "TU": "Tuesday",
    "WE": "Wednesday",
    "TH": "Thursday",
    "FR": "Friday",
    "SA": "Saturday",
    "SU": "Sunday",
}


def describe(rule: str, start: datetime | None = None) -> str:
    """'FREQ=WEEKLY;BYDAY=TU,TH' -> 'every Tuesday and Thursday at 18:00'. Pure presentation; the RRULE is the truth."""
    parts = dict(p.split("=", 1) for p in normalise_rrule(rule).split(";") if "=" in p)
    freq = parts.get("FREQ", "")
    interval = int(parts.get("INTERVAL", "1") or 1)
    byday = [d for d in parts.get("BYDAY", "").split(",") if d]
    at = f" at {start.strftime('%H:%M')}" if start else ""
    if freq == "DAILY":
        text = "every day" if interval == 1 else f"every {interval} days"
    elif freq == "WEEKLY" and sorted(byday) == sorted(_DAYS[:5]):
        text = "every weekday"
    elif freq == "WEEKLY" and byday:
        names = [_NAMES.get(d, d) for d in sorted(byday, key=_DAYS.index)]
        joined = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        text = f"every {joined}" if interval == 1 else f"every {interval} weeks on {joined}"
    elif freq == "WEEKLY":
        text = "every week" if interval == 1 else f"every {interval} weeks"
    elif freq == "MONTHLY":
        text = "every month" if interval == 1 else f"every {interval} months"
    elif freq == "YEARLY":
        text = "every year"
    else:
        text = rule
    if "COUNT" in parts:
        text += f", {parts['COUNT']} times"
    if "UNTIL" in parts:
        u = parts["UNTIL"]
        text += f", until {u[0:4]}-{u[4:6]}-{u[6:8]}"
    return text + at
