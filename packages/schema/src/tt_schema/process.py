"""§5 delivery process"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from tt_schema.base import Base, utcnow
from tt_schema.enums import *  # noqa: F403


class Process(Base):
    workspace_id: str
    key: str
    name: str
    description: Optional[str] = None
    applies_to: list[TargetType] = Field(default_factory=lambda: [TargetType.WORK_ITEM])
    is_default: bool = False
    owner_id: Optional[str] = None


class Stage(Base):
    process_id: str
    key: str
    name: str
    position: int
    category: StageCategory
    maps_to_state_category: StateCategory  # required (v0.4 decision 1)
    color: Optional[str] = None
    entry_criteria: Optional[list[str]] = None
    exit_criteria: Optional[list[str]] = None
    expected_duration_days: Optional[int] = None
    wip_limit: Optional[int] = None
    requires_approval: bool = False


class StagePlacement(Base):
    target_type: TargetType
    target_id: str
    process_id: str
    stage_id: str
    entered_at: datetime = Field(default_factory=utcnow)
    exited_at: Optional[datetime] = None
    moved_by: Optional[str] = None
    reason: Optional[str] = None
    approval_id: Optional[str] = None

    @property
    def is_current(self) -> bool:
        return self.exited_at is None


class StageTransitionRule(Base):
    process_id: str
    to_stage_id: str
    from_stage_id: Optional[str] = None
    allowed_roles: list[str] = Field(default_factory=list)
    required_evidence_kinds: Optional[list[EvidenceKind]] = None
    auto_when: Optional[dict[str, Any]] = None
