"""The personal timer (Phase 15 §15.1/§15.2): one running timer; starting another pauses the current one.

This is the server's own rule (`activity.paused` with reason `auto_switch`), kept locally so the timer works with no server at all.
When the item was already sent as a work item, the caller may mirror `start/pause/done` to the server through the ordinary outbox
(see `tt_client.personal.send.timer_message`); nothing here talks to a network.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from tt_client.personal.model import PersonalItem, Segment, Status, Timer, utcnow
from tt_client.personal.recurrence import split_instance_id
from tt_client.personal.store import PersonalStore

Clock = Callable[[], datetime]


class TimerError(Exception):
    pass


class TimerEngine:
    def __init__(self, store: PersonalStore, clock: Clock = utcnow) -> None:
        self.store, self.clock = store, clock

    # -- queries
    def current(self) -> Timer | None:
        return self.store.running_timer()

    def elapsed_min(self, item_id: str) -> int:
        t = self.store.get_timer(item_id)
        return t.elapsed_min(self.clock()) if t else 0

    # -- transitions
    def start(self, item_id: str) -> tuple[Timer, Timer | None]:
        """Start (or resume) the timer of `item_id`. Returns (this timer, the timer that was paused to make room, if any)."""
        item = self.store.get_item(item_id)
        if item is None and not split_instance_id(item_id):
            raise TimerError(f"no such item: {item_id}")
        now = self.clock()
        paused: Timer | None = None
        running = self.store.running_timer()
        if running is not None and running.item_id != item_id:
            paused = self._pause(running, now)
        timer = self.store.get_timer(item_id)
        if timer is None:
            timer = Timer(item_id=item_id, started_at=now)
        if timer.state == "running":
            return timer, paused
        timer.paused_at = None
        timer.stopped_at = None
        timer.segments.append(Segment(start=now))
        self.store.put_timer(timer)
        self._mark(item_id, Status.ACTIVE)
        return timer, paused

    def pause(self, item_id: str | None = None) -> Timer | None:
        timer = self.store.get_timer(item_id) if item_id else self.store.running_timer()
        if timer is None or timer.state != "running":
            return timer
        return self._pause(timer, self.clock())

    def stop(self, item_id: str | None = None) -> tuple[Timer, int] | None:
        """Close the open segment, mark the timer stopped and record the cumulative minutes on the item."""
        timer = (
            self.store.get_timer(item_id) if item_id else (self.store.running_timer() or self._last_paused())
        )
        if timer is None or timer.state == "stopped":
            return None
        now = self.clock()
        if timer.segments and timer.segments[-1].end is None:
            timer.segments[-1].end = now
        timer.paused_at, timer.stopped_at = None, now
        self.store.put_timer(timer)
        minutes = timer.elapsed_min(now)
        item = self.store.get_item(timer.item_id)
        if item is not None:
            item.actual_min = minutes
            if item.status == Status.ACTIVE:
                item.status = Status.PLANNED
            self.store.put_item(item)
        return timer, minutes

    # -- helpers
    def _pause(self, timer: Timer, now: datetime) -> Timer:
        if timer.segments and timer.segments[-1].end is None:
            timer.segments[-1].end = now
        timer.paused_at = now
        self.store.put_timer(timer)
        item = self.store.get_item(timer.item_id)
        if item is not None and item.status == Status.ACTIVE:
            item.status = Status.PLANNED
            self.store.put_item(item)
        return timer

    def _last_paused(self) -> Timer | None:
        paused = [t for t in self.store.timers() if t.state == "paused"]
        return max(paused, key=lambda t: t.paused_at or t.started_at) if paused else None

    def _mark(self, item_id: str, status: Status) -> None:
        item: PersonalItem | None = self.store.get_item(item_id)
        if item is not None:
            item.status = status
            self.store.put_item(item)
