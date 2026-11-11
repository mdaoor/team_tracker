"""Declarative base, shared mixins and custom column types."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import UserDefinedType
from tt_schema.base import new_id

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def _values(enum_cls: type[enum.Enum]) -> Sequence[Any]:
    """Store enum *values* (not member names) so rows match the API/JSON representation."""
    return [member.value for member in enum_cls]


class LTree(UserDefinedType[str]):
    """PostgreSQL `ltree`; values are plain dotted strings (`root.child.grandchild`)."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "ltree"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class WorkspaceScopedMixin:
    """Row-level tenancy: every repository query is scoped by `workspace_id` (architecture §4.6)."""

    workspace_id: Mapped[str] = mapped_column(
        sa.Text(), sa.ForeignKey("workspaces.id"), nullable=False, index=True
    )


class RowBase(Base, TimestampMixin, SoftDeleteMixin):
    """Abstract base: `Base` columns of the schema package (id, created_*, updated_at, deleted_at)."""

    __abstract__ = True
    # fetch server defaults (created_at/updated_at) with RETURNING so async code never lazy-loads them
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[str] = mapped_column(sa.Text(), primary_key=True, default=new_id)
    created_by: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
