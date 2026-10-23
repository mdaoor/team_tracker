"""SQLAlchemy models for tt_schema (generated once, hand-owned afterwards). No business logic."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.enums import (
    ContextKind,
    MembershipRole,
    MemberStatus,
    PersonKind,
    ReportingKind,
    ScopeType,
    TeamKind,
    Visibility,
)

from tt_server.db.base import RowBase, WorkspaceScopedMixin, _values


class Person(RowBase):
    __tablename__ = "people"
    display_name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    email: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    kind: Mapped[PersonKind] = mapped_column(
        sa.Enum(
            PersonKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=PersonKind.HUMAN,
    )
    time_zone: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="UTC")
    is_active: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    __table_args__ = (sa.UniqueConstraint("email"),)


class Workspace(RowBase):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    slug: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    settings: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    __table_args__ = (sa.UniqueConstraint("slug"),)


class WorkspaceMember(WorkspaceScopedMixin, RowBase):
    __tablename__ = "workspace_members"
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    handle: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    capacity_hours_per_week: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)
    cost_rate: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)
    status: Mapped[MemberStatus] = mapped_column(
        sa.Enum(
            MemberStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=MemberStatus.ACTIVE,
    )
    inbox_project_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("projects.id"), nullable=True
    )
    __table_args__ = (
        sa.UniqueConstraint("workspace_id", "person_id"),
        sa.UniqueConstraint("workspace_id", "handle"),
    )


class Team(WorkspaceScopedMixin, RowBase):
    __tablename__ = "teams"
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    slug: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    kind: Mapped[TeamKind] = mapped_column(
        sa.Enum(
            TeamKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=TeamKind.ORG,
    )
    parent_team_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("teams.id"), nullable=True)
    lead_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    valid_to: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    __table_args__ = (sa.UniqueConstraint("workspace_id", "slug"),)


class Membership(RowBase):
    __tablename__ = "memberships"
    team_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("teams.id"), nullable=False)
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    role: Mapped[MembershipRole] = mapped_column(
        sa.Enum(
            MembershipRole,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=MembershipRole.MEMBER,
    )
    allocation_pct: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class ReportingLine(WorkspaceScopedMixin, RowBase):
    __tablename__ = "reporting_lines"
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    manager_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    kind: Mapped[ReportingKind] = mapped_column(
        sa.Enum(
            ReportingKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ReportingKind.LINE,
    )
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
        default=ScopeType.NONE,
    )
    scope_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    is_primary: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    valid_from: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    valid_to: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)


class ContextNote(WorkspaceScopedMixin, RowBase):
    __tablename__ = "context_notes"
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
    author_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    kind: Mapped[ContextKind] = mapped_column(
        sa.Enum(
            ContextKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ContextKind.OTHER,
    )
    pinned: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
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
        default=Visibility.INHERIT,
    )
    valid_from: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    valid_to: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
