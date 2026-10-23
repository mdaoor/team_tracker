"""SQLAlchemy models for tt_schema (generated once, hand-owned afterwards). No business logic."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.enums import ProductLinkRole, ProductStatus, ScopeType, TargetType, ValueSource, Visibility

from tt_server.db.base import RowBase, WorkspaceScopedMixin, _values


class Product(WorkspaceScopedMixin, RowBase):
    __tablename__ = "products"
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    owner_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    parent_product_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("products.id"), nullable=True
    )
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    status: Mapped[ProductStatus] = mapped_column(
        sa.Enum(
            ProductStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ProductStatus.ACTIVE,
    )
    default_process_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("processes.id"), nullable=True
    )
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
        default=Visibility.WORKSPACE,
    )
    __table_args__ = (sa.UniqueConstraint("workspace_id", "key"),)


class ProductLink(RowBase):
    __tablename__ = "product_links"
    product_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("products.id"), nullable=False)
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
    role: Mapped[ProductLinkRole] = mapped_column(
        sa.Enum(
            ProductLinkRole,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ProductLinkRole.DELIVERS,
    )
    weight: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)


class Objective(WorkspaceScopedMixin, RowBase):
    __tablename__ = "objectives"
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
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    period: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    owner_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    status: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="active")


class KeyResult(RowBase):
    __tablename__ = "key_results"
    objective_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("objectives.id"), nullable=False)
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    target_value: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False)
    unit: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    current_value: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False, default=Decimal("0"))
    metric_key: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    source: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="manual")


class WeightScheme(WorkspaceScopedMixin, RowBase):
    __tablename__ = "weight_schemes"
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
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
    formula: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)


class Weight(RowBase):
    __tablename__ = "weights"
    work_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    scheme_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("weight_schemes.id"), nullable=False)
    value: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False)
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
        default=ValueSource.FORMULA,
    )
    set_by: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
