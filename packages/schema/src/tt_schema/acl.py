"""§10 access control"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import Field

from tt_schema.base import Base
from tt_schema.enums import *  # noqa: F403


class Role(Base):
    key: str
    name: str
    permissions: list[str] = Field(default_factory=list)
    workspace_id: Optional[str] = None


class RoleAssignment(Base):
    person_id: str
    role_id: str
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: Optional[str] = None


class AccessGrant(Base):
    workspace_id: str
    subject_type: SubjectType
    subject_id: str
    object_type: TargetType
    object_id: str
    level: GrantLevel = GrantLevel.READ
    inherit_to_children: bool = True
    granted_by: Optional[str] = None
    expires_at: Optional[datetime] = None
