"""§7 projects"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import Field

from tt_schema.base import Base, utcnow
from tt_schema.enums import *  # noqa: F403


class Project(Base):
    workspace_id: str
    key: str
    name: str
    owner_id: str
    workflow_id: str
    default_item_type_id: str
    parent_project_id: Optional[str] = None
    description: Optional[str] = None
    kind: ProjectKind = ProjectKind.STANDARD
    methodology: Methodology = Methodology.NONE
    status: ProjectStatus = ProjectStatus.ACTIVE
    team_id: Optional[str] = None
    default_process_id: Optional[str] = None
    planned_start: Optional[date] = None
    planned_end: Optional[date] = None
    actual_start: Optional[date] = None
    actual_end: Optional[date] = None
    visibility: Visibility = Visibility.MEMBERS
    is_provisional: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)
    external_refs: dict[str, str] = Field(default_factory=dict)  # {"jira": "WEB"}


class ProjectMember(Base):
    project_id: str
    person_id: str
    role: ProjectRole = ProjectRole.CONTRIBUTOR
    allocation_pct: Optional[int] = None


class WorkItemType(Base):
    workspace_id: str
    key: ItemTypeKey
    name: str
    level: int
    project_id: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    estimation_unit: EstimationUnit = EstimationUnit.HOURS
    default_workflow_id: Optional[str] = None
    required_fields: list[str] = Field(default_factory=list)


class Workflow(Base):
    workspace_id: str
    name: str
    is_default: bool = False


class WorkflowState(Base):
    workflow_id: str
    key: str
    name: str
    category: StateCategory
    position: int
    wip_limit: Optional[int] = None
    is_initial: bool = False
    is_terminal: bool = False


class Iteration(Base):
    project_id: str
    name: str
    start_date: date
    end_date: date
    goal: Optional[str] = None
    state: IterationState = IterationState.PLANNED
    committed_points: Optional[Decimal] = None
    completed_points: Optional[Decimal] = None


class Milestone(Base):
    project_id: str
    name: str
    due_date: date
    description: Optional[str] = None
    completed_at: Optional[datetime] = None
    kind: MilestoneKind = MilestoneKind.DEADLINE
    approval_id: Optional[str] = None


class Component(Base):
    project_id: str
    name: str
    lead_id: Optional[str] = None


class Release(Base):
    project_id: str
    name: str
    release_date: Optional[date] = None
    state: ReleaseState = ReleaseState.UNRELEASED


class Risk(Base):
    project_id: str
    key: str
    title: str
    owner_id: str
    target_type: TargetType = TargetType.PROJECT
    target_id: Optional[str] = None
    description: Optional[str] = None
    category: RiskCategory = RiskCategory.TECHNICAL
    probability: int = 3  # 1-5
    impact: int = 3  # 1-5
    status: RiskStatus = RiskStatus.IDENTIFIED
    trigger: Optional[str] = None
    response_strategy: RiskResponse = RiskResponse.MITIGATE
    review_date: Optional[date] = None

    @property
    def score(self) -> int:
        return self.probability * self.impact


class RiskLink(Base):
    risk_id: str
    work_item_id: str
    inherited: bool = False


class RiskAction(Base):
    risk_id: str
    description: str
    work_item_id: Optional[str] = None
    due_date: Optional[date] = None
    status: str = "open"


class Approval(Base):
    project_id: str
    target_type: TargetType
    target_id: str
    title: str
    requested_by: str
    policy: ApprovalPolicy = ApprovalPolicy.ANY_ONE
    quorum_n: Optional[int] = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = Field(default_factory=utcnow)
    resolved_at: Optional[datetime] = None


class ApprovalDecision(Base):
    approval_id: str
    approver_id: str
    decision: Decision = Decision.PENDING
    comment: Optional[str] = None
    decided_at: Optional[datetime] = None


class RecurrenceRule(Base):
    project_id: str
    template_item_id: str
    rrule: str  # iCal RRULE
    time_zone: str = "UTC"
    assign_to: dict[str, Any] = Field(default_factory=dict)
    lead_days: int = 0
    next_run_at: Optional[datetime] = None
    is_active: bool = True
