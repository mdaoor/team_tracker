"""Risk register tables: risks, their links to other records and their mitigation actions."""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.enums import (
    RiskCategory,
    RiskResponse,
    RiskStatus,
    TargetType,
)

from tt_server.db.base import RowBase, _values


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
