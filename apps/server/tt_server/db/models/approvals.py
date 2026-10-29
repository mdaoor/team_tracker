"""Approvals with their per-approver decisions, and recurrence rules."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.base import utcnow
from tt_schema.enums import (
    ApprovalPolicy,
    ApprovalStatus,
    Decision,
    TargetType,
)

from tt_server.db.base import RowBase, _values


class Approval(RowBase):
    __tablename__ = "approvals"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
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
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    requested_by: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    policy: Mapped[ApprovalPolicy] = mapped_column(
        sa.Enum(
            ApprovalPolicy,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ApprovalPolicy.ANY_ONE,
    )
    quorum_n: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        sa.Enum(
            ApprovalStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    requested_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class ApprovalDecision(RowBase):
    __tablename__ = "approval_decisions"
    approval_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("approvals.id"), nullable=False)
    approver_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    decision: Mapped[Decision] = mapped_column(
        sa.Enum(
            Decision,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=Decision.PENDING,
    )
    comment: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class RecurrenceRule(RowBase):
    __tablename__ = "recurrence_rules"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
    template_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    rrule: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    time_zone: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="UTC")
    assign_to: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    lead_days: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    next_run_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
