"""§9 views"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from tt_schema.base import Base
from tt_schema.enums import *  # noqa: F403


class SavedView(Base):
    workspace_id: str
    owner_id: str
    name: str
    entity: TargetType = TargetType.WORK_ITEM
    scope_type: ScopeType = ScopeType.PERSON
    scope_id: Optional[str] = None
    filter: dict[str, Any] = Field(default_factory=dict)
    group_by: list[str] = Field(default_factory=list)
    sort_by: list[str] = Field(default_factory=list)
    layout: ViewLayout = ViewLayout.LIST
    weight_scheme_id: Optional[str] = None
    is_shared: bool = False
