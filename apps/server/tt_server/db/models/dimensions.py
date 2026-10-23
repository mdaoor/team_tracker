"""SQLAlchemy models for tt_schema (generated once, hand-owned afterwards). No business logic."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.enums import Cardinality, DimensionKind, TargetType, ValueSource, ValueTypeShape

from tt_server.db.base import RowBase, WorkspaceScopedMixin, _values


class Dimension(WorkspaceScopedMixin, RowBase):
    __tablename__ = "dimensions"
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    kind: Mapped[DimensionKind] = mapped_column(
        sa.Enum(
            DimensionKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=DimensionKind.CUSTOM,
    )
    value_type: Mapped[ValueTypeShape] = mapped_column(
        sa.Enum(
            ValueTypeShape,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ValueTypeShape.FLAT,
    )
    cardinality: Mapped[Cardinality] = mapped_column(
        sa.Enum(
            Cardinality,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=Cardinality.SINGLE,
    )
    applies_to: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=lambda: ["work_item"])
    is_required_for: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    owner_role: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    __table_args__ = (sa.UniqueConstraint("workspace_id", "key"),)


class DimensionValue(RowBase):
    __tablename__ = "dimension_values"
    dimension_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("dimensions.id"), nullable=False)
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    parent_value_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("dimension_values.id"), nullable=True
    )
    color: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    backing_type: Mapped[TargetType | None] = mapped_column(
        sa.Enum(
            TargetType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=True,
    )
    backing_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    position: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    __table_args__ = (sa.UniqueConstraint("dimension_id", "key"),)


class Classification(WorkspaceScopedMixin, RowBase):
    __tablename__ = "classifications"
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
    dimension_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("dimensions.id"), nullable=False)
    value_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("dimension_values.id"), nullable=False)
    source: Mapped[ValueSource] = mapped_column(
        sa.Enum(
            ValueSource,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ValueSource.MANUAL,
    )
    inherited_from_type: Mapped[TargetType | None] = mapped_column(
        sa.Enum(
            TargetType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=True,
    )
    inherited_from_id: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    set_by: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    set_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)
    is_confirmed: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=True)
    confirmed_by: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    __table_args__ = (sa.UniqueConstraint("target_type", "target_id", "dimension_id", "value_id"),)
