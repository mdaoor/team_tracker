"""§7 capture layer and the event log"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import Field

from tt_schema.base import Base, utcnow
from tt_schema.enums import *  # noqa: F403


class Activity(Base):
    person_id: str
    work_item_id: str
    project_id: str
    kind: ActivityKind = ActivityKind.WORKING
    status: ActivityStatus = ActivityStatus.ACTIVE
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: Optional[datetime] = None
    note: Optional[str] = None
    client_meta: Optional[dict[str, Any]] = None
    mood: Optional[int] = None


class Evidence(Base):
    activity_id: str
    work_item_id: str
    person_id: str
    kind: EvidenceKind = EvidenceKind.NOTE
    title: Optional[str] = None
    body: Optional[str] = None
    url: Optional[str] = None
    attachment_id: Optional[str] = None
    captured_at: datetime = Field(default_factory=utcnow)
    source: CaptureSource = CaptureSource.MANUAL


class TimeEntry(Base):
    person_id: str
    work_item_id: str
    date: date
    minutes: int
    activity_id: Optional[str] = None
    is_billable: bool = False
    note: Optional[str] = None
    source: CaptureSource = CaptureSource.TIMER


class DailyLog(Base):
    workspace_id: str
    person_id: str
    date: date
    summary: str = ""
    item_ids: list[str] = Field(default_factory=list)
    minutes_total: int = 0
    blockers: Optional[str] = None
    status: DailyLogStatus = DailyLogStatus.DRAFT
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class Event(Base):
    workspace_id: str
    actor_id: str
    entity_type: TargetType
    entity_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: EventSource = EventSource.CLIENT
    occurred_at: datetime = Field(default_factory=utcnow)
    received_at: datetime = Field(default_factory=utcnow)
    client_id: Optional[str] = None
    raw_input: Optional[str] = None
    correlation_id: Optional[str] = None
