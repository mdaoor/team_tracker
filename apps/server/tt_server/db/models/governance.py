"""SQLAlchemy models for tt_schema (generated once, hand-owned afterwards). No business logic."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.base import utcnow
from tt_schema.enums import (
    HabitStatus,
    InsightKind,
    InsightStatus,
    Polarity,
    PolicyCategory,
    PolicyTrigger,
    ScopeType,
    Severity,
    TargetType,
    Trend,
    ViolationStatus,
)

from tt_server.db.base import RowBase, WorkspaceScopedMixin, _values


class PolicyTemplate(RowBase):
    __tablename__ = "policy_templates"
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    category: Mapped[PolicyCategory] = mapped_column(
        sa.Enum(
            PolicyCategory,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    cel_source: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    params: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("workspaces.id"), nullable=True)
    is_builtin: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    published_scope: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="workspace")


class Policy(WorkspaceScopedMixin, RowBase):
    __tablename__ = "policies"
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    target: Mapped[TargetType] = mapped_column(
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
    owner_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    condition_cel: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    scope_type: Mapped[ScopeType] = mapped_column(
        sa.Enum(
            ScopeType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ScopeType.WORKSPACE,
    )
    scope_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    trigger: Mapped[PolicyTrigger] = mapped_column(
        sa.Enum(
            PolicyTrigger,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=PolicyTrigger.ON_EVENT,
    )
    event_types: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    schedule: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    condition_template_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    template_params: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    candidate_filter: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    context_schema_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=1)
    severity: Mapped[Severity] = mapped_column(
        sa.Enum(
            Severity,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=Severity.WARNING,
    )
    category: Mapped[PolicyCategory] = mapped_column(
        sa.Enum(
            PolicyCategory,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=PolicyCategory.PROCESS,
    )
    notify: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    auto_action: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    grace_period: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)


class PolicyTestRun(RowBase):
    __tablename__ = "policy_test_runs"
    policy_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("policies.id"), nullable=False)
    window: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    fired_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    sample_target_ids: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)
    ran_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)


class Violation(WorkspaceScopedMixin, RowBase):
    __tablename__ = "violations"
    policy_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("policies.id"), nullable=False)
    target_type: Mapped[TargetType] = mapped_column(
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
    target_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    severity: Mapped[Severity] = mapped_column(
        sa.Enum(
            Severity,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    category: Mapped[PolicyCategory] = mapped_column(
        sa.Enum(
            PolicyCategory,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    person_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    team_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("teams.id"), nullable=True)
    project_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=True)
    product_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("products.id"), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    evidence: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    status: Mapped[ViolationStatus] = mapped_column(
        sa.Enum(
            ViolationStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ViolationStatus.OPEN,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    waived_by: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    waive_reason: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    __table_args__ = (
        sa.Index("ix_violations_workspace_status_severity", "workspace_id", "status", "severity"),
        # one open violation per (policy, target): the engine's de-duplication is also a database rule
        sa.Index(
            "uq_violations_open_target",
            "policy_id",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=sa.text("status IN ('open', 'acknowledged') AND deleted_at IS NULL"),
        ),
    )


class HabitDefinition(RowBase):
    __tablename__ = "habit_definitions"
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    polarity: Mapped[Polarity] = mapped_column(
        sa.Enum(
            Polarity,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    formula: Mapped[Any] = mapped_column(JSONB(), nullable=False)
    thresholds: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    category: Mapped[PolicyCategory] = mapped_column(
        sa.Enum(
            PolicyCategory,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=PolicyCategory.PROCESS,
    )
    workspace_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("workspaces.id"), nullable=True)


class Habit(WorkspaceScopedMixin, RowBase):
    __tablename__ = "habits"
    subject_type: Mapped[TargetType] = mapped_column(
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
    subject_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    polarity: Mapped[Polarity] = mapped_column(
        sa.Enum(
            Polarity,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    window: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="30d")
    occurrences: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    rate: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False, default=Decimal("0"))
    trend: Mapped[Trend] = mapped_column(
        sa.Enum(
            Trend,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=Trend.STABLE,
    )
    score: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False, default=Decimal("0"))
    first_seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    linked_violation_ids: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)
    status: Mapped[HabitStatus] = mapped_column(
        sa.Enum(
            HabitStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=HabitStatus.OBSERVED,
    )
    coaching_note: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    category: Mapped[PolicyCategory] = mapped_column(
        sa.Enum(
            PolicyCategory,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=PolicyCategory.PROCESS,
    )
    __table_args__ = (
        sa.UniqueConstraint(
            "workspace_id", "subject_type", "subject_id", "key", "window", name="uq_habits_subject_key_window"
        ),
    )


class Insight(RowBase):
    __tablename__ = "insights"
    scope_type: Mapped[ScopeType] = mapped_column(
        sa.Enum(
            ScopeType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    scope_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    period: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    kind: Mapped[InsightKind] = mapped_column(
        sa.Enum(
            InsightKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    supporting_ids: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)
    generated_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    status: Mapped[InsightStatus] = mapped_column(
        sa.Enum(
            InsightStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=InsightStatus.NEW,
    )


class MetricSnapshot(RowBase):
    __tablename__ = "metric_snapshots"
    scope_type: Mapped[ScopeType] = mapped_column(
        sa.Enum(
            ScopeType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    scope_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    date: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    weight_scheme_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("weight_schemes.id"), nullable=True
    )
    metrics: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
