"""§6 products, objectives, weights"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import Field

from tt_schema.base import Base, utcnow
from tt_schema.enums import *  # noqa: F403


class Product(Base):
    workspace_id: str
    key: str
    name: str
    owner_id: str
    parent_product_id: Optional[str] = None
    description: Optional[str] = None
    status: ProductStatus = ProductStatus.ACTIVE
    default_process_id: Optional[str] = None
    visibility: Visibility = Visibility.WORKSPACE


class ProductLink(Base):
    product_id: str
    target_type: TargetType
    target_id: str
    role: ProductLinkRole = ProductLinkRole.DELIVERS
    weight: Optional[Decimal] = None


class Objective(Base):
    workspace_id: str
    scope_type: ScopeType
    scope_id: str
    title: str
    period: str  # e.g. "2026-Q4"
    owner_id: str
    status: str = "active"


class KeyResult(Base):
    objective_id: str
    title: str
    target_value: Decimal
    unit: str
    current_value: Decimal = Decimal(0)
    metric_key: Optional[str] = None
    source: str = "manual"  # manual | metric_snapshot


class WeightScheme(Base):
    workspace_id: str
    name: str
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: Optional[str] = None
    formula: dict[str, Any] = Field(default_factory=dict)


class Weight(Base):
    work_item_id: str
    scheme_id: str
    value: Decimal
    source: ValueSource = ValueSource.FORMULA
    set_by: Optional[str] = None
    computed_at: datetime = Field(default_factory=utcnow)
