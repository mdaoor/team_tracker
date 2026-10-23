"""SQLAlchemy models for tt_schema (generated once, hand-owned afterwards). No business logic."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.enums import GrantLevel, ScopeType, SubjectType, TargetType

from tt_server.db.base import RowBase, WorkspaceScopedMixin, _values


class Role(RowBase):
    __tablename__ = "roles"
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    permissions: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)
    workspace_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("workspaces.id"), nullable=True)


class RoleAssignment(RowBase):
    __tablename__ = "role_assignments"
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    role_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("roles.id"), nullable=False)
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


class AccessGrant(WorkspaceScopedMixin, RowBase):
    __tablename__ = "access_grants"
    subject_type: Mapped[SubjectType] = mapped_column(
        sa.Enum(
            SubjectType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    subject_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    object_type: Mapped[TargetType] = mapped_column(
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
    object_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    level: Mapped[GrantLevel] = mapped_column(
        sa.Enum(
            GrantLevel,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=GrantLevel.READ,
    )
    inherit_to_children: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    granted_by: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
