"""Notification port (Phase 13 R6.7): the one way client code asks the surface to tell the person something.

The CLI prints, the desktop shell shows a system notification (Phase 11), Slack sends a DM. Policy lives here (when is a
nudge due, never twice a day); delivery lives in the adapter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo


@dataclass
class Notification:
    key: str  # stable id of the nudge, e.g. "eod:2026-10-23"
    title: str
    body: str
    actions: list[str] = field(default_factory=list)  # command names the surface can offer: "today --confirm"


class NotificationPort(Protocol):
    def notify(self, n: Notification) -> None: ...


class ConsoleNotifier:
    """The CLI adapter: one dim line on stderr-like output through the supplied `echo`."""

    def __init__(self, echo: Any) -> None:
        self.echo = echo

    def notify(self, n: Notification) -> None:
        hint = f"  (tt {n.actions[0]})" if n.actions else ""
        self.echo(f"• {n.title} {n.body}{hint}".rstrip())


class NudgeLog:
    """Remembers which nudges were shown so each fires once."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> set[str]:
        try:
            return set(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            return set()

    def seen(self, key: str) -> bool:
        return key in self._load()

    def mark(self, key: str) -> None:
        keys = sorted(self._load() | {key})[-200:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(keys))


def end_of_day_due(local_now: datetime, daily_log_time: str = "17:30") -> bool:
    hh, mm = (int(x) for x in daily_log_time.split(":"))
    return (local_now.hour, local_now.minute) >= (hh, mm)


def end_of_day_nudge(
    port: NotificationPort,
    log: NudgeLog,
    api: Any,
    now: datetime,
    tz: str,
    daily_log_time: str = "17:30",
) -> bool:
    """Ask once per local day, after `daily_log_time`, when there is a draft nobody has confirmed. True if it asked."""
    local = now.astimezone(ZoneInfo(tz))
    day = local.date().isoformat()
    key = f"eod:{day}"
    if not end_of_day_due(local, daily_log_time) or log.seen(key):
        return False
    try:
        logs = api.daily_logs(day)
    except Exception:  # noqa: BLE001 - a nudge must never break the command that triggered it
        return False
    if not logs or logs[0].get("status") != "draft":
        return False
    hours = (logs[0].get("minutes_total") or 0) / 60
    port.notify(
        Notification(key, "Confirm today's log?", f"{hours:.1f} h across your work today.", ["today --confirm"])
    )
    log.mark(key)
    return True
