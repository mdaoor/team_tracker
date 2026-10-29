"""SQLAlchemy models for tt_schema (generated once, hand-owned afterwards). No business logic."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.enums import (
    EstimationUnit,
    ItemTypeKey,
    IterationState,
    Methodology,
    MilestoneKind,
    ProjectKind,
    ProjectRole,
    ProjectStatus,
    ReleaseState,
    StateCategory,
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
