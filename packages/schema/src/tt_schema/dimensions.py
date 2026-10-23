"""§4 dimensions"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import Field

from tt_schema.base import Base, utcnow
from tt_schema.enums import *  # noqa: F403


class Dimension(Base):
    workspace_id: str
    key: str
    name: str
    kind: DimensionKind = DimensionKind.CUSTOM
    value_type: ValueTypeShape = ValueTypeShape.FLAT
    cardinality: Cardinality = Cardinality.SINGLE
    applies_to: list[TargetType] = Field(default_factory=lambda: [TargetType.WORK_ITEM])
    is_required_for: Optional[dict[str, Any]] = None
    owner_role: Optional[str] = None


class DimensionValue(Base):
    dimension_id: str
    key: str
    name: str
    parent_value_id: Optional[str] = None
    color: Optional[str] = None
    backing_type: Optional[TargetType] = None
    backing_id: Optional[str] = None
    position: int = 0
    is_active: bool = True


class Classification(Base):
    workspace_id: str
    target_type: TargetType
    target_id: str
    dimension_id: str
    value_id: str
    source: ValueSource = ValueSource.MANUAL
    inherited_from_type: Optional[TargetType] = None
    inherited_from_id: Optional[str] = None
    set_by: Optional[str] = None
    set_at: datetime = Field(default_factory=utcnow)
    confidence: Optional[Decimal] = None
    is_confirmed: bool = True
    confirmed_by: Optional[str] = None
