"""Day / week / month views, totals, capacity and the end-of-day and weekly review cards (Phase 15 §15.2). Pure functions over the store."""

from __future__ import annotations

import calendar as _cal
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from tt_client.personal.model import Category, Kind, PersonalItem, Status, Timer
from tt_client.personal.recurrence import materialise, resolve_local, zone
from tt_client.personal.settings import Settings
from tt_client.personal.store import PersonalStore

CATS = [c.value for c in Category]
HABIT_KINDS = {Kind.HABIT, Kind.SPORT, Kind.READING, Kind.COURSE, Kind.TRAINING}


def day_bounds(day: date, tz: str) -> tuple[datetime, datetime]:
    z = zone(tz)
    return resolve_local(datetime.combine(day, time()), z), resolve_local(
        datetime.combine(day + timedelta(days=1), time()), z
    )


def items_between(store: PersonalStore, start: datetime, end: datetime) -> list[PersonalItem]:
    """Everything that starts in [start, end): stored one-off items plus materialised instances of every series."""
    out: list[PersonalItem] = []
    for it in store.items_on_days(start.astimezone(UTC).date(), end.astimezone(UTC).date()):
        if it.rrule or it.series_id:
            continue  # masters and overrides come through the series branch
        if it.start and start <= it.start < end:
            out.append(it)
    for master in store.series():
        out.extend(materialise(master, start, end, store.overrides_for(master.id)))
    return sorted(out, key=lambda i: (i.all_day is False, i.start or start, i.title))


def minutes_planned(item: PersonalItem) -> int:
    if item.status in (Status.CANCELLED, Status.SKIPPED):
        return 0
    return item.duration_min or 0


def minutes_done(item: PersonalItem, now: datetime) -> int:
    """Done = marked done (timer/actual/planned minutes), or a meeting that has taken place."""
    if item.status == Status.DONE:
        return item.minutes or 0
    if (
        item.kind == Kind.MEETING
        and item.status == Status.PLANNED
        and (end := item.effective_end)
        and end <= now
    ):
        return item.attended_min or item.duration_min or 0
    return 0


def item_row(
    item: PersonalItem, now: datetime, timer: Timer | None = None, tz: str = "UTC"
) -> dict[str, Any]:
    z = zone(tz)
    return {
        "id": item.id,
        "title": item.title,
        "kind": item.kind.value,
        "category": item.category.value,
        "status": item.status.value,
        "start": item.start.astimezone(z).isoformat()
        if item.start
        else None,  # shown in the person's zone, stored as UTC
        "end": (item.effective_end.astimezone(z).isoformat() if item.effective_end else None),
        "all_day": item.all_day,
        "duration_min": item.duration_min,
        "recurring": bool(item.series_id or item.rrule),
        "series_id": item.series_id or (item.id if item.rrule else None),
        "tags": item.tags,
        "project_hint": item.project_hint,
        "share_policy": item.share_policy.value,
        "sent": item.is_sent,
        "sendable": item.category != Category.PERSONAL,
        "online_meeting_url": item.online_meeting_url,
        "location": item.location,
        "attendees": item.attendees,
        "from_calendar": item.calendar_ref.provider if item.calendar_ref else None,
        "minutes_planned": minutes_planned(item),
        "minutes_done": minutes_done(item, now),
        "timer": timer.state if timer else None,
        "elapsed_min": timer.elapsed_min(now) if timer else 0,
    }


def capacity_min_for(day: date, settings: Settings, pack_capacity: float | None = None) -> int:
    weekly = pack_capacity if pack_capacity else float(settings.get("capacity_hours_per_week"))
    workweek = list(settings.get("workweek"))
    return round(weekly * 60 / max(1, len(workweek))) if day.weekday() in workweek else 0


def _totals(items: list[PersonalItem], now: datetime) -> dict[str, dict[str, int]]:
    t = {c: {"planned": 0, "done": 0} for c in CATS}
    for it in items:
        t[it.category.value]["planned"] += minutes_planned(it)
        t[it.category.value]["done"] += minutes_done(it, now)
    return t


def day_view(
    store: PersonalStore,
    day: date,
    tz: str,
    now: datetime,
    settings: Settings,
    *,
    server_items: list[dict[str, Any]] | None = None,
    pack_capacity: float | None = None,
) -> dict[str, Any]:
    start, end = day_bounds(day, tz)
    items = items_between(store, start, end)
    timers = {t.item_id: t for t in store.timers()}
    rows = [item_row(i, now, timers.get(i.id), tz) for i in items]
    running = store.running_timer()
    cap = capacity_min_for(day, settings, pack_capacity)
    totals = _totals(items, now)
    planned = sum(v["planned"] for v in totals.values())
    return {
        "date": day.isoformat(),
        "tz": tz,
        "timeline": [r for r in rows if not r["all_day"]],
        "anytime": [r for r in rows if r["all_day"]],
        "server_items": server_items
        or [],  # assigned/sent work items from the ContextPack, shown distinct from personal ones
        "totals": totals,
        "planned_min": planned,
        "capacity_min": cap,
        "load_pct": round(100 * planned / cap) if cap else (100 if planned else 0),
        "timer": {"item_id": running.item_id, "elapsed_min": running.elapsed_min(now)} if running else None,
    }


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_view(
    store: PersonalStore,
    day: date,
    tz: str,
    now: datetime,
    settings: Settings,
    *,
    pack_capacity: float | None = None,
) -> dict[str, Any]:
    first = week_start(day)
    days = [
        day_view(store, first + timedelta(days=i), tz, now, settings, pack_capacity=pack_capacity)
        for i in range(7)
    ]
    totals = {
        c: {
            "planned": sum(d["totals"][c]["planned"] for d in days),
            "done": sum(d["totals"][c]["done"] for d in days),
        }
        for c in CATS
    }
    return {
        "week_start": first.isoformat(),
        "days": days,
        "totals": totals,
        "planned_min": sum(d["planned_min"] for d in days),
        "capacity_min": sum(d["capacity_min"] for d in days),
    }


def month_view(
    store: PersonalStore, year: int, month: int, tz: str, now: datetime, settings: Settings
) -> dict[str, Any]:
    first = date(year, month, 1)
    last = date(year, month, _cal.monthrange(year, month)[1])
    start, _ = day_bounds(first, tz)
    _, end = day_bounds(last, tz)
    items = items_between(store, start, end)
    z = zone(tz)
    per_day: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"dots": set(), "minutes": defaultdict(int), "sent_min": 0, "private_min": 0}
    )
    for it in items:
        if it.start is None:
            continue
        d = it.start.astimezone(z).date().isoformat()
        mins = minutes_done(it, now) or minutes_planned(it)
        row = per_day[d]
        row["dots"].add(it.category.value)
        row["minutes"][it.category.value] += mins
        if it.is_sent:
            row["sent_min"] += sum(r.minutes or 0 for r in it.sent_refs if r.unsent_at is None)
        else:
            row["private_min"] += mins
    days = {
        d: {
            "dots": sorted(v["dots"]),
            "minutes": dict(v["minutes"]),
            "sent_min": v["sent_min"],
            "private_min": v["private_min"],
        }
        for d, v in sorted(per_day.items())
    }
    totals_h = {c: round(sum(v["minutes"].get(c, 0) for v in per_day.values()) / 60, 1) for c in CATS}
    cal = _cal.Calendar(firstweekday=0).monthdatescalendar(year, month)
    return {
        "year": year,
        "month": month,
        "weeks": [[d.isoformat() for d in w] for w in cal],
        "days": days,
        "totals_h": totals_h,
        "sparkline": [
            {"date": d, "sent": v["sent_min"], "private": v["private_min"]} for d, v in days.items()
        ],
    }


# ---------------------------------------------------------------------------------------------------- review cards
def streaks(
    store: PersonalStore, today: date, tz: str, now: datetime, lookback_days: int = 60
) -> list[dict[str, Any]]:
    """Per recurring habit-like series: the current run of completed instances and the completion rate in the lookback."""
    out: list[dict[str, Any]] = []
    z = zone(tz)
    start, _ = day_bounds(today - timedelta(days=lookback_days), tz)
    for master in store.series():
        if master.kind not in HABIT_KINDS:
            continue
        instances = [
            i
            for i in materialise(master, start, now, store.overrides_for(master.id))
            if i.status != Status.CANCELLED
        ]
        past = [
            i for i in instances if i.instance_date and i.instance_date < today or i.status == Status.DONE
        ]
        run = 0
        for inst in reversed(past):
            if inst.status == Status.DONE:
                run += 1
            else:
                break
        done = sum(1 for i in past if i.status == Status.DONE)
        out.append(
            {"series_id": master.id, "title": master.title, "streak": run, "done": done, "of": len(past)}
        )
        _ = z
    return sorted(out, key=lambda r: (-r["streak"], r["title"]))


@dataclass
class ReviewInputs:
    store: PersonalStore
    settings: Settings
    tz: str
    now: datetime
    pack_capacity: float | None = None


def end_of_day_card(inp: ReviewInputs, day: date, unsent: list[dict[str, Any]]) -> dict[str, Any]:
    start, end = day_bounds(day, inp.tz)
    items = items_between(inp.store, start, end)
    totals = _totals(items, inp.now)
    return {
        "date": day.isoformat(),
        "hours_by_category": {c: round(totals[c]["done"] / 60, 2) for c in CATS},
        "planned_by_category": {c: round(totals[c]["planned"] / 60, 2) for c in CATS},
        "done_count": sum(1 for i in items if i.status == Status.DONE),
        "open_count": sum(
            1 for i in items if i.status in (Status.PLANNED, Status.ACTIVE) and i.kind != Kind.MEETING
        ),
        "unsent": unsent,  # tray-shaped rows of today's unsent work items: [Send all] [Pick] [Skip]
        "sent_count": sum(1 for i in items if i.is_sent),
    }


def weekly_review_card(inp: ReviewInputs, day: date) -> dict[str, Any]:
    first = week_start(day)
    start, _ = day_bounds(first, inp.tz)
    _, end = day_bounds(first + timedelta(days=6), inp.tz)
    items = items_between(inp.store, start, end)
    totals = _totals(items, inp.now)
    planned = sum(v["planned"] for v in totals.values())
    done = sum(v["done"] for v in totals.values())
    cap = sum(capacity_min_for(first + timedelta(days=i), inp.settings, inp.pack_capacity) for i in range(7))
    meetings_min = totals["meeting"]["planned"]
    return {
        "week_start": first.isoformat(),
        "planned_h": round(planned / 60, 1),
        "done_h": round(done / 60, 1),
        "completion_pct": round(100 * done / planned) if planned else 0,
        "development_h": round(totals["development"]["done"] / 60, 1),
        "meetings_h": round(meetings_min / 60, 1),
        "meeting_load_pct": round(100 * meetings_min / cap) if cap else 0,
        "streaks": streaks(inp.store, min(day, inp.now.astimezone(zone(inp.tz)).date()), inp.tz, inp.now),
        "development_items": [
            {"title": i.title, "hours": round(minutes_done(i, inp.now) / 60, 2)}
            for i in items
            if i.category == Category.DEVELOPMENT and minutes_done(i, inp.now)
        ],
    }
