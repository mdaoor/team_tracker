"""SQLAlchemy models for tt_schema (generated once, hand-owned afterwards). No business logic."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.enums import (
    ActivityKind,
    ActivityStatus,
    CaptureSource,
    DailyLogStatus,
    EventSource,
    EvidenceKind,
    TargetType,
)

from tt_server.db.base import RowBase, WorkspaceScopedMixin, _values


class Activity(RowBase):
    __tablename__ = "activities"
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    work_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
    kind: Mapped[ActivityKind] = mapped_column(
        sa.Enum(
            ActivityKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ActivityKind.WORKING,
    )
    status: Mapped[ActivityStatus] = mapped_column(
        sa.Enum(
            ActivityStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ActivityStatus.ACTIVE,
    )
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    client_meta: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    mood: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    __table_args__ = (
        sa.Index("ix_activities_person_active", "person_id", postgresql_where=sa.text("status = 'active'")),
    )


class Evidence(RowBase):
    __tablename__ = "evidence"
    activity_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("activities.id"), nullable=False)
    work_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    kind: Mapped[EvidenceKind] = mapped_column(
        sa.Enum(
            EvidenceKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=EvidenceKind.NOTE,
    )
    title: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    body: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    url: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    attachment_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("attachments.id"), nullable=True
    )
    captured_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    source: Mapped[CaptureSource] = mapped_column(
        sa.Enum(
            CaptureSource,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=CaptureSource.MANUAL,
    )


class TimeEntry(RowBase):
    __tablename__ = "time_entries"
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    work_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    date: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    minutes: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    activity_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("activities.id"), nullable=True)
    is_billable: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    source: Mapped[CaptureSource] = mapped_column(
        sa.Enum(
            CaptureSource,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=CaptureSource.TIMER,
    )


class DailyLog(WorkspaceScopedMixin, RowBase):
    __tablename__ = "daily_logs"
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    date: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    summary: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="")
    item_ids: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)
    minutes_total: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    blockers: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    status: Mapped[DailyLogStatus] = mapped_column(
        sa.Enum(
            DailyLogStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=DailyLogStatus.DRAFT,
    )
    reviewed_by: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class Event(WorkspaceScopedMixin, RowBase):
    __tablename__ = "events"
    actor_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    entity_type: Mapped[TargetType] = mapped_column(
        sa.Enum(
            TargetType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    entity_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    payload: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    source: Mapped[EventSource] = mapped_column(
        sa.Enum(
            EventSource,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=EventSource.CLIENT,
    )
    occurred_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    client_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    raw_input: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    __table_args__ = (
        sa.Index("ix_events_workspace_occurred", "workspace_id", sa.text("occurred_at DESC")),
        sa.Index("ix_events_entity", "entity_type", "entity_id"),
        sa.Index(
            "uq_events_client_id", "client_id", unique=True, postgresql_where=sa.text("client_id IS NOT NULL")
        ),
    )
