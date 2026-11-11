"""§8 governance"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import Field

from tt_schema.base import Base, utcnow
from tt_schema.enums import *  # noqa: F403


class PolicyTemplate(Base):
    key: str
    name: str
    category: PolicyCategory
    cel_source: str
    params: list[dict[str, Any]] = Field(default_factory=list)  # [{name,type,default,min,max}]
    description: Optional[str] = None
    workspace_id: Optional[str] = None
    is_builtin: bool = False
    published_scope: str = "workspace"  # workspace | shared


class Policy(Base):
    workspace_id: str
    key: str
    name: str
    target: TargetType
    owner_id: str
    condition_cel: str
    description: Optional[str] = None
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: Optional[str] = None
    trigger: PolicyTrigger = PolicyTrigger.ON_EVENT
    event_types: Optional[list[str]] = None
    schedule: Optional[str] = None  # cron
    condition_template_id: Optional[str] = None
    template_params: Optional[dict[str, Any]] = None
    candidate_filter: Optional[dict[str, Any]] = None
    context_schema_version: int = 1
    severity: Severity = Severity.WARNING
    category: PolicyCategory = PolicyCategory.PROCESS
    notify: dict[str, Any] = Field(default_factory=dict)
    auto_action: Optional[dict[str, Any]] = None
    grace_period: Optional[str] = None  # ISO-8601 duration
    is_active: bool = True


class PolicyTestRun(Base):
    policy_id: str
    window: str
    fired_count: int
    sample_target_ids: list[str] = Field(default_factory=list)
    ran_at: datetime = Field(default_factory=utcnow)


class Violation(Base):
    workspace_id: str
    policy_id: str
    target_type: TargetType
    target_id: str
    severity: Severity
    category: PolicyCategory
    person_id: Optional[str] = None
    team_id: Optional[str] = None
    project_id: Optional[str] = None
    product_id: Optional[str] = None
    detected_at: datetime = Field(default_factory=utcnow)
    evidence: dict[str, Any] = Field(default_factory=dict)
    status: ViolationStatus = ViolationStatus.OPEN
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolution_note: Optional[str] = None
    waived_by: Optional[str] = None
    waive_reason: Optional[str] = None


class HabitDefinition(Base):
    key: str
    name: str
    polarity: Polarity
    formula: dict[str, Any]
    thresholds: dict[str, Any] = Field(default_factory=dict)
    category: PolicyCategory = PolicyCategory.PROCESS
    workspace_id: Optional[str] = None


class Habit(Base):
    workspace_id: str
    subject_type: TargetType
    subject_id: str
    key: str
    polarity: Polarity
    window: str = "30d"
    occurrences: int = 0
    rate: Decimal = Decimal(0)
    trend: Trend = Trend.STABLE
    score: Decimal = Decimal(0)
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    linked_violation_ids: list[str] = Field(default_factory=list)
    status: HabitStatus = HabitStatus.OBSERVED
    coaching_note: Optional[str] = None
    category: PolicyCategory = PolicyCategory.PROCESS  # wellbeing → managers-only rule in ACL


class Insight(Base):
    scope_type: ScopeType
    scope_id: str
    period: str
    kind: InsightKind
    body: str
    supporting_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utcnow)
    status: InsightStatus = InsightStatus.NEW


class MetricSnapshot(Base):
    scope_type: ScopeType
    scope_id: str
    date: date
    weight_scheme_id: Optional[str] = None
    metrics: dict[str, Any] = Field(default_factory=dict)
