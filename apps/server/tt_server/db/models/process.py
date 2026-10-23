"""SQLAlchemy models for tt_schema (generated once, hand-owned afterwards). No business logic."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.enums import StageCategory, StateCategory, TargetType

from tt_server.db.base import RowBase, WorkspaceScopedMixin, _values


class Process(WorkspaceScopedMixin, RowBase):
    __tablename__ = "processes"
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    applies_to: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=lambda: ["work_item"])
    is_default: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    owner_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    __table_args__ = (sa.UniqueConstraint("workspace_id", "key"),)


class Stage(RowBase):
    __tablename__ = "stages"
    process_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("processes.id"), nullable=False)
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    position: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    category: Mapped[StageCategory] = mapped_column(
        sa.Enum(
            StageCategory,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    maps_to_state_category: Mapped[StateCategory] = mapped_column(
        sa.Enum(
            StateCategory,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    color: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    entry_criteria: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    exit_criteria: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    expected_duration_days: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    wip_limit: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    requires_approval: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    __table_args__ = (sa.UniqueConstraint("process_id", "key"),)


class StagePlacement(RowBase):
    __tablename__ = "stage_placements"
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
    process_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("processes.id"), nullable=False)
    stage_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("stages.id"), nullable=False)
    entered_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    exited_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    moved_by: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    reason: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("approvals.id"), nullable=True)
    __table_args__ = (
        ExcludeConstraint(
            ("target_type", "="),
            ("target_id", "="),
            ("process_id", "="),
            (sa.text("tstzrange(entered_at, exited_at, '[)')"), "&&"),
            using="gist",
            name="ex_stage_placements_one_current",
        ),
    )


class StageTransitionRule(RowBase):
    __tablename__ = "stage_transition_rules"
    process_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("processes.id"), nullable=False)
    to_stage_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("stages.id"), nullable=False)
    from_stage_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("stages.id"), nullable=True)
    allowed_roles: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)
    required_evidence_kinds: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    auto_when: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
