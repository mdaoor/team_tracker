"""Calendar sync into the personal store (Phase 15 §15.3): filters, upsert by `calendar_ref`, cursors, and the poll scheduler.

* Declined, cancelled, all-day, private, out-of-office and focus-time events are skipped by default (each is a setting).
* A meeting is stored as `PersonalItem{kind=meeting, category=meeting}` with `calendar_ref`; local choices (project, category,
  done/skipped, notes) survive a re-sync, provider fields (title, time, attendees, link) follow the provider.
* Cursors (`syncToken`, `deltaLink`) live in the encrypted store; a change in the filter settings forces one full pull so that events that
  were skipped before can appear (and vice versa).
* A failing provider never raises into the UI: the error is kept in the sync state and shown ("calendar sync failed: ...").
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from tt_client.integrations.base import (
    AuthRequired,
    CalendarEvent,
    CalendarProvider,
    ProviderError,
    RateLimited,
)
from tt_client.personal.model import CalendarLink, Category, Kind, PersonalItem, SharePolicy, Status
from tt_client.personal.settings import Settings
from tt_client.personal.store import PersonalStore

log = logging.getLogger("tt_client.calendar")
FILTER_KEYS = [
    "calendar.skip_declined",
    "calendar.skip_cancelled",
    "calendar.skip_all_day",
    "calendar.skip_private",
    "calendar.skip_ooo",
    "calendar.skip_focus",
]
WINDOW_BACK_DAYS, WINDOW_AHEAD_DAYS = 14, 21


def skip_reason(ev: CalendarEvent, s: Settings) -> str | None:
    if ev.status == "cancelled" and s.get("calendar.skip_cancelled"):
        return "cancelled"
    if ev.response == "declined" and s.get("calendar.skip_declined"):
        return "declined"
    if ev.all_day and s.get("calendar.skip_all_day"):
        return "all-day"
    if ev.is_private and s.get("calendar.skip_private"):
        return "private"
    if ev.event_type == "outOfOffice" and s.get("calendar.skip_ooo"):
        return "out of office"
    if ev.event_type == "focusTime" and s.get("calendar.skip_focus"):
        return "focus time"
    if ev.event_type == "workingLocation":
        return "working location"
    return None


def to_item(ev: CalendarEvent, tz: str) -> PersonalItem:
    return PersonalItem(
        title=ev.title,
        kind=Kind.MEETING,
        category=Category.MEETING,
        start=ev.start,
        end=ev.end,
        all_day=ev.all_day,
        tz=tz,
        status=Status.PLANNED,
        share_policy=SharePolicy.ASK,
        attendees=ev.attendees,
        organizer=ev.organizer,
        location=ev.location,
        online_meeting_url=ev.online_url,
        attended_min=ev.attended_min,
        calendar_ref=CalendarLink(
            provider=ev.provider,
            calendar_id=ev.calendar_id,
            event_id=ev.event_id,
            ical_uid=ev.ical_uid,
            etag=ev.etag,
            series_id=ev.series_id,
            instance_start=ev.instance_start or ev.start,
        ),
    )


@dataclass
class SyncReport:
    provider: str
    added: int = 0
    updated: int = 0
    removed: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    calendars: int = 0
    error: str | None = None
    retry_after: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _fingerprint(s: Settings) -> str:
    return hashlib.sha256(
        json.dumps([s.get(k) for k in FILTER_KEYS] + [sorted(s.get("calendar.excluded"))]).encode()
    ).hexdigest()[:16]


class CalendarSync:
    def __init__(
        self,
        store: PersonalStore,
        providers: dict[str, CalendarProvider],
        *,
        tz: str = "UTC",
        me: dict[str, str] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.store, self.providers, self.tz, self.me, self.clock = store, providers, tz, me or {}, clock
        self.settings = Settings(store)

    def _index(self) -> dict[tuple[str, str, str], PersonalItem]:
        return {
            (i.calendar_ref.provider, i.calendar_ref.calendar_id, i.calendar_ref.event_id): i
            for i in self.store.items(include_deleted=True)
            if i.calendar_ref is not None
        }

    def sync(self, provider: str, *, full: bool = False) -> SyncReport:
        report = SyncReport(provider)
        prov = self.providers.get(provider)
        if prov is None:
            report.error = f"{provider} is not configured"
            return report
        state = self.store.get_calendar_state(provider)
        now = self.clock()
        if full or state.get("fingerprint") != _fingerprint(self.settings):
            state["cursors"] = {}
        try:
            calendars = state.get("calendars") or [c.__dict__ for c in prov.list_calendars()]
            state["calendars"] = calendars
            excluded = set(self.settings.get("calendar.excluded"))
            window = (now - timedelta(days=WINDOW_BACK_DAYS), now + timedelta(days=WINDOW_AHEAD_DAYS))
            index = self._index()
            for cal in calendars:
                if cal["id"] in excluded or not cal.get("selected", True):
                    continue
                report.calendars += 1
                page = prov.sync(
                    cal["id"],
                    cursor=state.get("cursors", {}).get(cal["id"]),
                    window=window,
                    me=self.me.get(provider),
                )
                self._apply(prov, page.events, index, report, self.me.get(provider))
                state.setdefault("cursors", {})[cal["id"]] = page.cursor
            state["fingerprint"] = _fingerprint(self.settings)
            state["last_ok"] = now.isoformat()
            state["error"] = None
        except RateLimited as exc:
            report.error, report.retry_after = str(exc), exc.retry_after
            state["error"], state["retry_at"] = (
                str(exc),
                (now + timedelta(seconds=exc.retry_after)).isoformat(),
            )
        except AuthRequired as exc:
            report.error = str(exc)
            state["error"] = str(exc)
        except ProviderError as exc:
            report.error = str(exc)
            state["error"] = str(exc)
            log.warning("calendar sync failed (%s): %s", provider, exc)
        state["last_try"] = now.isoformat()
        self.store.set_calendar_state(provider, state)
        return report

    def _apply(
        self,
        prov: CalendarProvider,
        events: list[CalendarEvent],
        index: dict[tuple[str, str, str], PersonalItem],
        report: SyncReport,
        me: str | None,
    ) -> None:
        for ev in events:
            existing = index.get(ev.key)
            reason = "removed" if ev.removed else skip_reason(ev, self.settings)
            if reason is not None:
                if existing is not None and existing.deleted_at is None and not existing.is_sent:
                    self.store.delete_item(
                        existing.id
                    )  # it became declined/cancelled/private: drop it from the plan
                    report.removed += 1
                elif existing is None:
                    report.skipped[reason] = report.skipped.get(reason, 0) + 1
                continue
            if ev.online_url and ev.end <= self.clock():
                ev.attended_min = prov.attended_minutes(ev, me)
            if existing is None:
                item = self.store.put_item(to_item(ev, self.tz))
                index[ev.key] = item
                report.added += 1
            else:
                fresh = to_item(ev, self.tz)
                existing.deleted_at = None
                for f in (
                    "title",
                    "start",
                    "end",
                    "all_day",
                    "attendees",
                    "organizer",
                    "location",
                    "online_meeting_url",
                    "calendar_ref",
                ):
                    setattr(existing, f, getattr(fresh, f))
                existing.duration_min = fresh.duration_min
                if ev.attended_min is not None:
                    existing.attended_min = ev.attended_min
                self.store.put_item(existing)
                report.updated += 1

    def status(self, provider: str) -> dict[str, Any]:
        st = self.store.get_calendar_state(provider)
        return {k: st.get(k) for k in ("last_ok", "last_try", "error", "retry_at")}

    def disconnect(self, provider: str, delete_items: bool = True) -> int:
        """Forget the sync state and (by default) the meetings that came from this provider. Unsent meetings only."""
        self.store.delete_calendar_state(provider)
        n = 0
        if delete_items:
            for i in self.store.items():
                if i.calendar_ref and i.calendar_ref.provider == provider and not i.is_sent:
                    self.store.delete_item(i.id)
                    n += 1
        return n


class Scheduler:
    """Decides *when* to sync; the shell (desktop timer, `tt cal sync --watch`) calls `tick()` about once a minute.

    Due when: never synced, `poll_minutes` have passed (default 5), the machine woke up (a gap much longer than the poll interval between
    ticks) or on demand (`request()`). A rate-limited provider waits out its `Retry-After`.
    """

    def __init__(self, sync: CalendarSync, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.sync, self.clock = sync, clock
        self._last_tick: datetime | None = None
        self._requested = False

    def request(self) -> None:
        self._requested = True

    def due(self, provider: str, now: datetime | None = None) -> str | None:
        now = now or self.clock()
        poll = timedelta(minutes=int(self.sync.settings.get("calendar.poll_minutes")))
        st = self.sync.store.get_calendar_state(provider)
        if st.get("retry_at") and now < datetime.fromisoformat(st["retry_at"]):
            return None
        if self._requested:
            return "on demand"
        if not st.get("last_try"):
            return "first sync"
        if self._last_tick is not None and now - self._last_tick > max(poll * 3, timedelta(minutes=15)):
            return "wake"
        if now - datetime.fromisoformat(st["last_try"]) >= poll:
            return "poll"
        return None

    def tick(self) -> list[tuple[str, str, SyncReport]]:
        now = self.clock()
        ran: list[tuple[str, str, SyncReport]] = []
        for name in self.sync.providers:
            if self.sync.providers[name] is None:
                continue
            why = self.due(name, now)
            if why:
                ran.append((name, why, self.sync.sync(name)))
        self._requested = False
        self._last_tick = now
        return ran
