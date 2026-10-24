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
    ApprovalPolicy,
    ApprovalStatus,
    Decision,
    EstimationUnit,
    ItemTypeKey,
    IterationState,
    Methodology,
    MilestoneKind,
    ProjectKind,
    ProjectRole,
    ProjectStatus,
    ReleaseState,
    RiskCategory,
    RiskResponse,
    RiskStatus,
    StateCategory,
    TargetType,
    Visibility,
)

from tt_server.db.base import RowBase, WorkspaceScopedMixin, _values


class Project(WorkspaceScopedMixin, RowBase):
    __tablename__ = "projects"
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    owner_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    workflow_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("workflows.id"), nullable=False)
    default_item_type_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    parent_project_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("projects.id"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    kind: Mapped[ProjectKind] = mapped_column(
        sa.Enum(
            ProjectKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ProjectKind.STANDARD,
    )
    methodology: Mapped[Methodology] = mapped_column(
        sa.Enum(
            Methodology,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=Methodology.NONE,
    )
    status: Mapped[ProjectStatus] = mapped_column(
        sa.Enum(
            ProjectStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ProjectStatus.ACTIVE,
    )
    team_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("teams.id"), nullable=True)
    default_process_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("processes.id"), nullable=True
    )
    planned_start: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    planned_end: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    actual_start: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    actual_end: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    visibility: Mapped[Visibility] = mapped_column(
        sa.Enum(
            Visibility,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=Visibility.MEMBERS,
    )
    is_provisional: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    settings: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    external_refs: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    __table_args__ = (sa.UniqueConstraint("workspace_id", "key"),)


class ProjectMember(RowBase):
    __tablename__ = "project_members"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    role: Mapped[ProjectRole] = mapped_column(
        sa.Enum(
            ProjectRole,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ProjectRole.CONTRIBUTOR,
    )
    allocation_pct: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)


class WorkItemType(WorkspaceScopedMixin, RowBase):
    __tablename__ = "work_item_types"
    key: Mapped[ItemTypeKey] = mapped_column(
        sa.Enum(
            ItemTypeKey,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    level: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    project_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    icon: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    color: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    estimation_unit: Mapped[EstimationUnit] = mapped_column(
        sa.Enum(
            EstimationUnit,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=EstimationUnit.HOURS,
    )
    default_workflow_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("workflows.id"), nullable=True
    )
    required_fields: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)


class Workflow(WorkspaceScopedMixin, RowBase):
    __tablename__ = "workflows"
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    is_default: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)


class WorkflowState(RowBase):
    __tablename__ = "workflow_states"
    workflow_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("workflows.id"), nullable=False)
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    category: Mapped[StateCategory] = mapped_column(
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
    position: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    wip_limit: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    is_initial: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    is_terminal: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)


class Iteration(RowBase):
    __tablename__ = "iterations"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    start_date: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    end_date: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    goal: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    state: Mapped[IterationState] = mapped_column(
        sa.Enum(
            IterationState,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=IterationState.PLANNED,
    )
    committed_points: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)
    completed_points: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)


class Milestone(RowBase):
    __tablename__ = "milestones"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    due_date: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    kind: Mapped[MilestoneKind] = mapped_column(
        sa.Enum(
            MilestoneKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=MilestoneKind.DEADLINE,
    )
    approval_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("approvals.id"), nullable=True)


class Component(RowBase):
    __tablename__ = "components"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    lead_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)


class Release(RowBase):
    __tablename__ = "releases"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    release_date: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    state: Mapped[ReleaseState] = mapped_column(
        sa.Enum(
            ReleaseState,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ReleaseState.UNRELEASED,
    )


class Risk(RowBase):
    __tablename__ = "risks"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    owner_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
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
        default=TargetType.PROJECT,
    )
    target_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    category: Mapped[RiskCategory] = mapped_column(
        sa.Enum(
            RiskCategory,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=RiskCategory.TECHNICAL,
    )
    probability: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=3)
    impact: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=3)
    status: Mapped[RiskStatus] = mapped_column(
        sa.Enum(
            RiskStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=RiskStatus.IDENTIFIED,
    )
    trigger: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    response_strategy: Mapped[RiskResponse] = mapped_column(
        sa.Enum(
            RiskResponse,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=RiskResponse.MITIGATE,
    )
    review_date: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    __table_args__ = (sa.UniqueConstraint("project_id", "key"),)


class RiskLink(RowBase):
    __tablename__ = "risk_links"
    risk_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("risks.id"), nullable=False)
    work_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    inherited: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)


class RiskAction(RowBase):
    __tablename__ = "risk_actions"
    risk_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("risks.id"), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    work_item_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=True)
    due_date: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="open")


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
