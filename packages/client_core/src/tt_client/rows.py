"""The client's one reading of three server responses (PR #11 review, UX Phase 14 items 1 and 3, Phase 15 item 1).

The CLI, the desktop sidecar and the Slack bot all used to read these payloads with guessed field names (`id`, `summary`,
`default_choice`, `person.handle`, ...) and printed `None`. The API shapes are mapped here, once, from the server's real rows
(`apps/server/tt_server/domain/overlay/review.py list_queue`, `domain/capture/activity_feed.py`, `dailylog.to_dict`); the tests
load responses recorded from the in-process server (`tools/record_client_fixtures.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _parse(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _age(since: datetime | None, now: datetime) -> str:
    """'3 min', '2 h', '1 d'; '' when unknown; 'just now' under a minute (clock skew included)."""
    if since is None:
        return ""
    ref = now if now.tzinfo else now.replace(tzinfo=UTC)
    minutes = int((ref - since).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min"
    if minutes < 60 * 24:
        return f"{minutes // 60} h"
    return f"{minutes // (60 * 24)} d"


def minutes_text(minutes: int) -> str:
    h, m = divmod(max(minutes, 0), 60)
    return f"{h} h" if h and not m else f"{h} h {m} min" if h else f"{m} min"


# ------------------------------------------------------------------ review queue
@dataclass(frozen=True)
class ReviewRow:
    """One row of `GET /review-queue`. `choice` is the server's suggested resolution (`suggested.choice`), or None."""

    ref: str
    item_key: str
    item_title: str
    reporter: str | None
    created_at: datetime | None
    kind: str
    choice: dict[str, Any] | None
    reason: str | None = None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> ReviewRow:
        suggested = d.get("suggested") or {}
        choice = suggested.get("choice")
        return cls(
            ref=str(d.get("ref") or ""),
            item_key=str(d.get("item_key") or ""),
            item_title=str(d.get("item_title") or ""),
            reporter=d.get("reporter"),
            created_at=_parse(d.get("created_at")),
            kind=str(d.get("kind") or ""),
            choice=dict(choice) if isinstance(choice, dict) and choice else None,
            reason=suggested.get("reason"),
        )

    @property
    def choice_label(self) -> str:
        """`WEB` for `{"project": "WEB"}`; several values are joined."""
        return " ".join(str(v) for v in (self.choice or {}).values() if v not in (None, ""))

    def line(self, now: datetime) -> str:
        """`INBOX-sara-1 · tidy the ingest retry · @sara · 3 min → WEB [Enter]`"""
        parts = [self.item_key, self.item_title]
        if self.reporter:
            parts.append(self.reporter)
        if age := _age(self.created_at, now):
            parts.append(age)
        head = " · ".join(p for p in parts if p)
        return (
            f"{head} → {self.choice_label} [Enter]"
            if self.choice
            else f"{head} · no suggestion (web console)"
        )

    def to_doc(self, now: datetime) -> dict[str, Any]:
        """What the sidecar hands the desktop palette."""
        return {
            "ref": self.ref,
            "item_key": self.item_key,
            "item_title": self.item_title,
            "reporter": self.reporter,
            "age": _age(self.created_at, now),
            "kind": self.kind,
            "choice": self.choice,
            "choice_label": self.choice_label or None,
            "reason": self.reason,
            "line": self.line(now),
        }


# ------------------------------------------------------------------ team now
@dataclass(frozen=True)
class TeamNowRow:
    """One row of `GET /activities?team=mine&status=active`."""

    person: str
    item_key: str
    item_title: str
    project_key: str | None
    started_at: str

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> TeamNowRow:
        who = d.get("person")
        handle = who.get("handle") if isinstance(who, dict) else who
        return cls(
            person=str(handle or "?"),
            item_key=str(d.get("item_key") or ""),
            item_title=str(d.get("item_title") or ""),
            project_key=d.get("project_key"),
            started_at=str(d.get("started_at") or ""),
        )

    def line(self, now: datetime) -> str:
        """`@sara · WEB-114 fix the login redirect loop · 12 min`"""
        what = f"{self.item_key} {self.item_title}".strip()
        age = _age(_parse(self.started_at), now)
        return " · ".join(p for p in (self.person, what, age) if p)

    def to_doc(self, now: datetime) -> dict[str, Any]:
        return {
            "person": self.person,
            "item_key": self.item_key,
            "item_title": self.item_title,
            "project_key": self.project_key,
            "age": _age(_parse(self.started_at), now),
            "line": self.line(now),
        }


# ------------------------------------------------------------------ the day
_KEY = re.compile(r"\b[A-Z][A-Z0-9]*-\d+\b")
_DURATION = re.compile(r"\d+(?:\.\d+)?\s*(?:h|min)\b")


@dataclass(frozen=True)
class DayDraft:
    """A `DailyLog` row (`GET /daily-logs?day=`)."""

    id: str
    date: str
    status: str
    minutes_total: int
    summary: str
    blockers: str | None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> DayDraft:
        return cls(
            id=str(d.get("id") or ""),
            date=str(d.get("date") or ""),
            status=str(d.get("status") or ""),
            minutes_total=int(d.get("minutes_total") or 0),
            summary=str(d.get("summary") or ""),
            blockers=d.get("blockers"),
        )

    def keys_worked(self) -> list[str]:
        """Item keys of the summary lines that carry time (a line without a duration is context, not work)."""
        timed = [line for line in self.summary.splitlines() if _DURATION.search(line)]
        keys = _KEY.findall("\n".join(timed) or self.summary)
        return list(dict.fromkeys(keys))

    def confirmed_sentence(self) -> str:
        """`Confirmed your day: 45 min on WEB-114.`"""
        keys = self.keys_worked()
        on = (
            ""
            if not keys
            else f" on {keys[0]}"
            if len(keys) == 1
            else f" on {keys[0]} and {keys[1]}"
            if len(keys) == 2
            else f" on {len(keys)} items"
        )
        return f"Confirmed your day: {minutes_text(self.minutes_total)}{on}."

    def already_sentence(self) -> str:
        return self.confirmed_sentence().replace("Confirmed your day", "Your day is already confirmed", 1)


__all__ = ["DayDraft", "ReviewRow", "TeamNowRow", "minutes_text"]
