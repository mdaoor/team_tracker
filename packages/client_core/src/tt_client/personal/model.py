"""The personal model (Phase 15 §15.1): items, timers, rules. Pure data; no I/O, no UI.

Names follow the plan. `category` carries the sharing default (ADR-0041); `personal` can never be sent: the model forces
`share_policy=never` and `tt_client.personal.send` has no path for it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from tt_schema.base import new_id
from tt_schema.enums import StrEnum


class Kind(StrEnum):
    TASK = "task"
    MEETING = "meeting"
    FOCUS = "focus"
    TRAINING = "training"
    COURSE = "course"
    READING = "reading"
    SPORT = "sport"
    HABIT = "habit"
    ERRAND = "errand"
    OTHER = "other"


class Category(StrEnum):
    WORK = "work"
    MEETING = "meeting"
    DEVELOPMENT = "development"
    PERSONAL = "personal"


class Status(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    DONE = "done"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class SharePolicy(StrEnum):
    NEVER = "never"
    ASK = "ask"
    AUTO = "auto"


# Which category a kind belongs to when nothing else says so (the quick-add parser and the rules engine override this).
KIND_CATEGORY: dict[Kind, Category] = {
    Kind.TASK: Category.WORK,
    Kind.MEETING: Category.MEETING,
    Kind.FOCUS: Category.WORK,
    Kind.TRAINING: Category.DEVELOPMENT,
    Kind.COURSE: Category.DEVELOPMENT,
    Kind.READING: Category.DEVELOPMENT,
    Kind.SPORT: Category.PERSONAL,
    Kind.HABIT: Category.PERSONAL,
    Kind.ERRAND: Category.PERSONAL,
    Kind.OTHER: Category.PERSONAL,
}

# Plan §15 principle 4: work -> ask once then remember; meeting -> suggest after each; development -> off; personal -> never.
CATEGORY_DEFAULT_SHARE: dict[Category, SharePolicy] = {
    Category.WORK: SharePolicy.ASK,
    Category.MEETING: SharePolicy.ASK,
    Category.DEVELOPMENT: SharePolicy.NEVER,
    Category.PERSONAL: SharePolicy.NEVER,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class SentRef(_M):
    client_id: str
    server_key: str | None = None
    at: datetime
    minutes: int | None = None
    project: str | None = None
    unsent_at: datetime | None = None  # set when the person used "Unsend"


class CalendarLink(_M):
    """Where a meeting came from. Local only; the server only ever receives `provider/event_id/series_id/instance_start`."""

    provider: Literal["google", "microsoft"]
    calendar_id: str
    event_id: str
    ical_uid: str | None = None
    etag: str | None = None
    series_id: str | None = None
    instance_start: datetime | None = None


class PersonalItem(_M):
    id: str = Field(default_factory=new_id)
    title: str
    kind: Kind = Kind.TASK
    category: Category = Category.WORK
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool = False
    duration_min: int | None = None
    tz: str = "UTC"
    rrule: str | None = (
        None  # iCal RRULE body without DTSTART (`FREQ=WEEKLY;BYDAY=TU,TH`); `start` is DTSTART in `tz` wall time
    )
    series_id: str | None = None  # master id for stored instance overrides
    instance_date: date | None = None  # local date of the occurrence this row overrides/represents
    status: Status = Status.PLANNED
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    project_hint: str | None = None
    item_hint: str | None = None  # server item key
    share_policy: SharePolicy = SharePolicy.NEVER
    sent_refs: list[SentRef] = Field(default_factory=list)
    calendar_ref: CalendarLink | None = None
    attendees: list[str] = Field(default_factory=list)
    organizer: str | None = None
    location: str | None = None
    online_meeting_url: str | None = None
    attended_min: int | None = None  # Teams attendance report, when available
    actual_min: int | None = None  # what the timer recorded (cumulative over its segments)
    send_each_instance: bool = False  # recurring development item: send every completed instance
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    deleted_at: datetime | None = None

    @model_validator(mode="after")
    def _invariants(self) -> PersonalItem:
        if (
            "share_policy" not in self.model_fields_set
        ):  # unless said otherwise, the category decides (plan principle 4)
            self.share_policy = CATEGORY_DEFAULT_SHARE[self.category]
        if self.category == Category.PERSONAL:  # no send path exists for personal items (ADR-0041)
            self.share_policy = SharePolicy.NEVER
            self.send_each_instance = False
        if self.start is not None and self.start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        if self.end is not None and self.end.tzinfo is None:
            raise ValueError("end must be timezone-aware")
        if (
            self.start is not None
        ):  # one representation everywhere: UTC instants (see recurrence.resolve_local)
            self.start = self.start.astimezone(UTC)
        if self.end is not None:
            self.end = self.end.astimezone(UTC)
        if self.start and self.end and self.end < self.start:
            raise ValueError("end before start")
        if self.duration_min is None and self.start and self.end and not self.all_day:
            self.duration_min = max(0, round((self.end - self.start).total_seconds() / 60))
        return self

    @property
    def is_recurring(self) -> bool:
        return bool(self.rrule) and self.series_id is None

    @property
    def is_sent(self) -> bool:
        return any(r.unsent_at is None for r in self.sent_refs)

    @property
    def minutes(self) -> int | None:
        """What a send reports: attended minutes, else what the timer recorded, else the scheduled/planned duration."""
        for v in (self.attended_min, self.actual_min, self.duration_min):
            if v:
                return v
        return None

    @property
    def effective_end(self) -> datetime | None:
        if self.end:
            return self.end
        if self.start and self.duration_min is not None:
            from datetime import timedelta

            return self.start + timedelta(minutes=self.duration_min)
        return None


class Segment(_M):
    start: datetime
    end: datetime | None = None

    def minutes(self, now: datetime) -> float:
        return max(0.0, ((self.end or now) - self.start).total_seconds() / 60)


class Timer(_M):
    """One timer per item; at most one is running at any time (starting another pauses the current one, the server's rule)."""

    item_id: str
    started_at: datetime
    paused_at: datetime | None = None
    stopped_at: datetime | None = None
    segments: list[Segment] = Field(default_factory=list)

    @property
    def state(self) -> Literal["running", "paused", "stopped"]:
        if self.stopped_at:
            return "stopped"
        return "running" if self.segments and self.segments[-1].end is None else "paused"

    def elapsed_min(self, now: datetime) -> int:
        return round(sum(s.minutes(now) for s in self.segments))


class RuleMatch(_M):
    kind: Kind | None = None
    title_regex: str | None = None
    attendee_domain: str | None = None
    calendar_id: str | None = None
    keyword: str | None = None
    series_id: str | None = None  # recurring series memory (learned)
    attendees: list[str] | None = None  # exact attendee set (lower-case emails), any order


class RuleSet(_M):
    category: Category | None = None
    project_hint: str | None = None
    share_policy: SharePolicy | None = None
    tags: list[str] = Field(default_factory=list)


class Rule(_M):
    id: str = Field(default_factory=new_id)
    match: RuleMatch
    set: RuleSet = Field(default_factory=RuleSet)
    origin: Literal["user", "learned"] = "user"
    hits: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class Draft(_M):
    """What the quick-add parser produced, before it becomes items (also the recurring editor's preview)."""

    title: str
    kind: Kind = Kind.TASK
    category: Category = Category.WORK
    start: datetime | None = None
    end: datetime | None = None
    duration_min: int | None = None
    all_day: bool = False
    rrule: str | None = None
    rrule_text: str | None = None  # human preview of the rule ("every weekday at 07:00")
    tags: list[str] = Field(default_factory=list)
    project_hint: str | None = None
    notes: str = ""
    warnings: list[str] = Field(default_factory=list)

    def to_item(self, tz: str) -> PersonalItem:
        return PersonalItem(
            title=self.title,
            kind=self.kind,
            category=self.category,
            start=self.start,
            end=self.end,
            all_day=self.all_day,
            duration_min=self.duration_min,
            tz=tz,
            rrule=self.rrule,
            tags=self.tags,
            project_hint=self.project_hint,
            notes=self.notes,
            share_policy=CATEGORY_DEFAULT_SHARE[self.category],
        )


def dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
