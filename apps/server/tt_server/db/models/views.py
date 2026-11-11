"""SQLAlchemy models for tt_schema (generated once, hand-owned afterwards). No business logic."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.enums import ScopeType, TargetType, ViewLayout

from tt_server.db.base import RowBase, WorkspaceScopedMixin, _values


class SavedView(WorkspaceScopedMixin, RowBase):
    __tablename__ = "saved_views"
    owner_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    entity: Mapped[TargetType] = mapped_column(
        sa.Enum(
            TargetType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=TargetType.WORK_ITEM,
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
        default=ScopeType.PERSON,
    )
    scope_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    filter: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    group_by: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)
    sort_by: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=list)
    layout: Mapped[ViewLayout] = mapped_column(
        sa.Enum(
            ViewLayout,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ViewLayout.LIST,
    )
    weight_scheme_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("weight_schemes.id"), nullable=True
    )
    is_shared: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
