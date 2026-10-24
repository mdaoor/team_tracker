"""§3 tenancy, people, hierarchy"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import Field

from tt_schema.base import Base, utcnow
from tt_schema.enums import *  # noqa: F403


class Person(Base):
    display_name: str
    email: str
    kind: PersonKind = PersonKind.HUMAN
    time_zone: str = "UTC"
    is_active: bool = True


class Workspace(Base):
    name: str
    slug: str
    settings: dict[str, Any] = Field(default_factory=dict)


class WorkspaceMember(Base):
    workspace_id: str
    person_id: str
    handle: str
    capacity_hours_per_week: Optional[Decimal] = None
    cost_rate: Optional[Decimal] = None
    status: MemberStatus = MemberStatus.ACTIVE
    inbox_project_id: Optional[str] = None


class Team(Base):
    workspace_id: str
    name: str
    slug: str
    kind: TeamKind = TeamKind.ORG
    parent_team_id: Optional[str] = None
    lead_id: Optional[str] = None
    description: Optional[str] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


class Membership(Base):
    team_id: str
    person_id: str
    role: MembershipRole = MembershipRole.MEMBER
    allocation_pct: Optional[int] = None
    joined_at: datetime = Field(default_factory=utcnow)
    left_at: Optional[datetime] = None


class ReportingLine(Base):
    workspace_id: str
    person_id: str
    manager_id: str
    kind: ReportingKind = ReportingKind.LINE
    scope_type: ScopeType = ScopeType.NONE
    scope_id: Optional[str] = None
    is_primary: bool = False
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


class ContextNote(Base):
    workspace_id: str
    scope_type: ScopeType
    scope_id: str
    author_id: str
    title: str
    body: str
    kind: ContextKind = ContextKind.OTHER
    pinned: bool = False
    visibility: Visibility = Visibility.INHERIT
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
