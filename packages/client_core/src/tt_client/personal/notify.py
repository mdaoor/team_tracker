"""When the desktop shell should tell the person something (Phase 15 §15.2/15.6, handover 14 L7): the policy, behind the `NotificationPort`.

Two nudges, both local (nothing here talks to a server):

* **Ready to send** - a finished meeting or a done work item enters the tray; the shell is told once ("Sprint planning ended: send 1 h to
  WEB?"). The once-only memory is `tray.notified` in the store.
* **End of day** - after `notify.eod_time` (default 17:30, empty = off) on a workday, once per local day, if something was done or is
  waiting in the tray ("How did today go?").

`notify_due(tracker, port)` hands the notifications to a port; the sidecar verb `personal_notify` uses `CollectingNotifier` and returns
them as JSON, the Tauri shell shows each with the OS notification API. The CLI could print them with `ConsoleNotifier` the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from tt_client.notify import Notification, NotificationPort
from tt_client.personal.model import Kind
from tt_client.personal.tray import _h, due_notifications

if TYPE_CHECKING:
    from tt_client.personal.service import Tracker


class CollectingNotifier:
    """A `NotificationPort` that keeps what it is given, for a caller (the sidecar) that relays it to the surface."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def notify(self, n: Notification) -> None:
        self.sent.append(n)

    def as_dicts(self) -> list[dict[str, object]]:
        return [
            {"key": n.key, "title": n.title, "body": n.body, "actions": list(n.actions)} for n in self.sent
        ]


def valid_clock(value: str) -> bool:
    """`HH:MM` on a 24-hour clock, or empty (off)."""
    if value == "":
        return True
    try:
        hh, mm = value.split(":")
        return len(hh) == 2 and len(mm) == 2 and 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except ValueError:
        return False


def ready_to_send(tracker: Tracker, port: NotificationPort) -> int:
    n = 0
    for e in due_notifications(tracker.store, tracker.tray()):
        it = e.item
        ended = it.kind == Kind.MEETING
        project = e.suggestion.project
        body = (
            f"Send {_h(e.minutes)} to {project}?" if project else f"Pick a project to send {_h(e.minutes)}."
        )
        port.notify(
            Notification(
                f"ready:{it.id}",
                f"{it.title} ended" if ended else f"{it.title} is done",
                body,
                [f"tray send {it.id[:8]}"],
            )
        )
        n += 1
    return n


def end_of_day(tracker: Tracker, port: NotificationPort) -> bool:
    settings = tracker.settings
    at = str(settings.get("notify.eod_time"))
    if not at:
        return False
    local = tracker.now.astimezone(ZoneInfo(tracker.env.tz))
    day = local.date()
    hh, mm = (int(x) for x in at.split(":"))
    if day.weekday() not in settings.get("workweek") or (local.hour, local.minute) < (hh, mm):
        return False
    if tracker.store.get_setting("notify.eod_last") == day.isoformat():
        return False
    card = tracker.end_of_day(day)
    waiting = len(card["unsent"])
    done_h = sum(card["hours_by_category"].values())
    if not waiting and not card["done_count"] and done_h <= 0:
        return False  # an empty day is not worth an interruption (and may still fill up before the evening is over)
    tracker.store.set_setting("notify.eod_last", day.isoformat())
    port.notify(
        Notification(
            f"eod:{day.isoformat()}",
            "How did today go?",
            f"{done_h:.1f} h done today · "
            + (f"{waiting} ready to send." if waiting else "nothing left to send."),
            ["eod"],
        )
    )
    return True


def notify_due(tracker: Tracker, port: NotificationPort) -> int:
    """Everything the shell should say now; each item at most once. Returns how many notifications were handed to the port."""
    return ready_to_send(tracker, port) + int(end_of_day(tracker, port))
