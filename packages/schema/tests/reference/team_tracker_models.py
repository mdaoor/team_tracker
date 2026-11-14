"""
Team Tracker — Python data classes (v0.4)

Reference implementation of team_tracker_object_model.md.
Pure dataclasses + enums, no ORM. Intended to be:
  * the shared schema package imported by client, server and LLM translator
  * the source for JSON Schema generation (see `to_json_schema()` stub at the bottom)

Conventions
  * ids are UUID v7 strings (time-sortable). `new_id()` generates them.
  * every persisted row inherits `Base` (id, created_at, updated_at, created_by, deleted_at)
  * Optional[...] == nullable column
  * enums are `str` subclasses so they serialise cleanly
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def new_id() -> str:
    """UUID v7-style (ms timestamp prefix + random). Good enough until stdlib ships uuid7."""
    ts = int(time.time() * 1000)
    rand = uuid.uuid4().int & ((1 << 80) - 1)
    return str(uuid.UUID(int=(ts << 80) | rand))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover
        return self.value


@dataclass(kw_only=True)
class Base:
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    created_by: Optional[str] = None
    deleted_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# enums
# ---------------------------------------------------------------------------

class PersonKind(StrEnum):
    HUMAN = "human"; BOT = "bot"; EXTERNAL = "external"

class MemberStatus(StrEnum):
    ACTIVE = "active"; INVITED = "invited"; SUSPENDED = "suspended"

class TeamKind(StrEnum):
    ORG = "org"; VIRTUAL = "virtual"; AD_HOC = "ad_hoc"

class MembershipRole(StrEnum):
    LEAD = "lead"; MEMBER = "member"; VIEWER = "viewer"

class ReportingKind(StrEnum):
    LINE = "line"; SENIOR = "senior"; PROJECT = "project"
    FUNCTIONAL = "functional"; MENTOR = "mentor"; DOTTED = "dotted"

class ScopeType(StrEnum):
    NONE = "none"; WORKSPACE = "workspace"; PROJECT = "project"; TEAM = "team"
    PERSON = "person"; PRODUCT = "product"; PROCESS = "process"; ITERATION = "iteration"

class ContextKind(StrEnum):
    GOAL = "goal"; OKR = "okr"; GUIDELINE = "guideline"; BACKGROUND = "background"
    DECISION = "decision"; LINK = "link"; ONBOARDING = "onboarding"; OTHER = "other"

class Visibility(StrEnum):
    INHERIT = "inherit"; PRIVATE = "private"; ASSIGNEES = "assignees"
    MEMBERS = "members"; TEAM = "team"; PROJECT = "project"; WORKSPACE = "workspace"
    SCOPE_MEMBERS = "scope_members"; MANAGERS_ONLY = "managers_only"

class ProjectKind(StrEnum):
    STANDARD = "standard"; INBOX = "inbox"; TEMPLATE = "template"

class Methodology(StrEnum):
    AGILE = "agile"; WATERFALL = "waterfall"; KANBAN = "kanban"; HYBRID = "hybrid"; NONE = "none"

class ProjectStatus(StrEnum):
    DRAFT = "draft"; ACTIVE = "active"; ON_HOLD = "on_hold"
    COMPLETED = "completed"; CANCELLED = "cancelled"; ARCHIVED = "archived"

class ProjectRole(StrEnum):
    OWNER = "owner"; MANAGER = "manager"; CONTRIBUTOR = "contributor"
    STAKEHOLDER = "stakeholder"; VIEWER = "viewer"

class ItemTypeKey(StrEnum):
    EPIC = "epic"; STORY = "story"; TASK = "task"; BUG = "bug"; SUBTASK = "subtask"
    SPIKE = "spike"; PHASE = "phase"; DELIVERABLE = "deliverable"; DECISION = "decision"

class EstimationUnit(StrEnum):
    POINTS = "points"; HOURS = "hours"; DAYS = "days"; NONE = "none"

class StateCategory(StrEnum):
    OPEN = "open"; ACTIVE = "active"; DONE = "done"

class IterationState(StrEnum):
    PLANNED = "planned"; ACTIVE = "active"; CLOSED = "closed"

class MilestoneKind(StrEnum):
    GATE = "gate"; RELEASE = "release"; DEMO = "demo"; DEADLINE = "deadline"; OTHER = "other"

class ReleaseState(StrEnum):
    UNRELEASED = "unreleased"; RELEASED = "released"; ARCHIVED = "archived"

class Priority(StrEnum):
    LOWEST = "lowest"; LOW = "low"; MEDIUM = "medium"
    HIGH = "high"; HIGHEST = "highest"; CRITICAL = "critical"

class ValueSource(StrEnum):
    MANUAL = "manual"; INHERITED = "inherited"; RULE = "rule"; IMPORT = "import"; FORMULA = "formula"

class ProgressMode(StrEnum):
    MANUAL = "manual"; CHILDREN = "children"; CHECKLIST = "checklist"; TIME = "time"

class LinkType(StrEnum):
    BLOCKS = "blocks"; IS_BLOCKED_BY = "is_blocked_by"; RELATES_TO = "relates_to"
    DUPLICATES = "duplicates"; CLONES = "clones"; DEPENDS_ON = "depends_on"
    FINISH_TO_START = "finish_to_start"; START_TO_START = "start_to_start"
    FINISH_TO_FINISH = "finish_to_finish"

class AssignmentRole(StrEnum):
    OWNER = "owner"; CONTRIBUTOR = "contributor"; REVIEWER = "reviewer"
    APPROVER = "approver"; WATCHER = "watcher"

class TagKind(StrEnum):
    LABEL = "label"; SKILL = "skill"; AREA = "area"; CLIENT = "client"; CUSTOM = "custom"

class EstimateKind(StrEnum):
    ORIGINAL = "original"; REVISED = "revised"; REMAINING = "remaining"

class TargetType(StrEnum):
    WORK_ITEM = "work_item"; PROJECT = "project"; PRODUCT = "product"; ACTIVITY = "activity"
    PERSON = "person"; RISK = "risk"; MILESTONE = "milestone"; RELEASE = "release"
    BASELINE = "baseline"; CONTEXT_NOTE = "context_note"; COMMENT = "comment"
    DAILY_LOG = "daily_log"; SAVED_VIEW = "saved_view"; STAGE_PLACEMENT = "stage_placement"
    TEAM = "team"; OBJECTIVE = "objective"

class AttachmentKind(StrEnum):
    FILE = "file"; LINK = "link"; IMAGE = "image"

class FieldType(StrEnum):
    TEXT = "text"; NUMBER = "number"; DATE = "date"; SELECT = "select"
    MULTI_SELECT = "multi_select"; PERSON = "person"; URL = "url"; BOOL = "bool"

class RiskCategory(StrEnum):
    SCHEDULE = "schedule"; SCOPE = "scope"; BUDGET = "budget"; TECHNICAL = "technical"
    RESOURCE = "resource"; EXTERNAL = "external"; COMPLIANCE = "compliance"

class RiskStatus(StrEnum):
    IDENTIFIED = "identified"; ANALYSING = "analysing"; MITIGATING = "mitigating"
    MONITORING = "monitoring"; OCCURRED = "occurred"; CLOSED = "closed"

class RiskResponse(StrEnum):
    AVOID = "avoid"; MITIGATE = "mitigate"; TRANSFER = "transfer"; ACCEPT = "accept"

class ApprovalPolicy(StrEnum):
    ANY_ONE = "any_one"; ALL = "all"; QUORUM = "quorum"

class ApprovalStatus(StrEnum):
    PENDING = "pending"; APPROVED = "approved"; REJECTED = "rejected"; CANCELLED = "cancelled"

class Decision(StrEnum):
    PENDING = "pending"; APPROVED = "approved"; REJECTED = "rejected"; ABSTAINED = "abstained"

class ActivityKind(StrEnum):
    WORKING = "working"; REVIEWING = "reviewing"; MEETING = "meeting"; BLOCKED = "blocked"
    RESEARCHING = "researching"; WAITING = "waiting"; OTHER = "other"

class ActivityStatus(StrEnum):
    ACTIVE = "active"; PAUSED = "paused"; ENDED = "ended"

class EvidenceKind(StrEnum):
    NOTE = "note"; SCREENSHOT = "screenshot"; LINK = "link"; COMMIT = "commit"
    PR = "pr"; DOCUMENT = "document"; FILE = "file"; METRIC = "metric"

class CaptureSource(StrEnum):
    MANUAL = "manual"; CLIENT_AUTO = "client_auto"; INTEGRATION = "integration"
    TIMER = "timer"; IMPORTED = "imported"

class DailyLogStatus(StrEnum):
    DRAFT = "draft"; CONFIRMED = "confirmed"; REVIEWED = "reviewed"

class EventSource(StrEnum):
    CLIENT = "client"; OVERLAY = "overlay"; PROPAGATION = "propagation"; RULE = "rule"; IMPORT = "import"

class DimensionKind(StrEnum):
    BUILTIN = "builtin"; CUSTOM = "custom"

class ValueTypeShape(StrEnum):
    TREE = "tree"; FLAT = "flat"

class Cardinality(StrEnum):
    SINGLE = "single"; MULTI = "multi"

class StageCategory(StrEnum):
    IDEATION = "ideation"; PLANNING = "planning"; EXECUTION = "execution"
    VALIDATION = "validation"; RELEASE = "release"; OPERATE = "operate"; CLOSED = "closed"

class ProductStatus(StrEnum):
    CONCEPT = "concept"; ACTIVE = "active"; SUNSET = "sunset"; RETIRED = "retired"

class ProductLinkRole(StrEnum):
    DELIVERS = "delivers"; SUPPORTS = "supports"; DEPENDS_ON = "depends_on"; OWNS = "owns"

class PolicyTrigger(StrEnum):
    ON_EVENT = "on_event"; SCHEDULED = "scheduled"

class Severity(StrEnum):
    INFO = "info"; WARNING = "warning"; VIOLATION = "violation"; CRITICAL = "critical"

class PolicyCategory(StrEnum):
    PROCESS = "process"; QUALITY = "quality"; TIME = "time"; PLANNING = "planning"
    COLLABORATION = "collaboration"; COMPLIANCE = "compliance"; WELLBEING = "wellbeing"

class ViolationStatus(StrEnum):
    OPEN = "open"; ACKNOWLEDGED = "acknowledged"; RESOLVED = "resolved"
    WAIVED = "waived"; FALSE_POSITIVE = "false_positive"

class Polarity(StrEnum):
    POSITIVE = "positive"; NEGATIVE = "negative"

class Trend(StrEnum):
    IMPROVING = "improving"; STABLE = "stable"; WORSENING = "worsening"

class HabitStatus(StrEnum):
    OBSERVED = "observed"; FLAGGED = "flagged"; IN_COACHING = "in_coaching"; RESOLVED = "resolved"

class ViewLayout(StrEnum):
    LIST = "list"; BOARD = "board"; GANTT = "gantt"; CALENDAR = "calendar"
    TIMELINE = "timeline"; TABLE = "table"; PIPELINE = "pipeline"; MATRIX = "matrix"

class GrantLevel(StrEnum):
    READ = "read"; COMMENT = "comment"; WRITE = "write"; ADMIN = "admin"

class SubjectType(StrEnum):
    PERSON = "person"; TEAM = "team"; ROLE = "role"

class InsightKind(StrEnum):
    SUMMARY = "summary"; ANOMALY = "anomaly"; RECOMMENDATION = "recommendation"

class InsightStatus(StrEnum):
    NEW = "new"; REVIEWED = "reviewed"; ACTIONED = "actioned"; DISMISSED = "dismissed"


# ---------------------------------------------------------------------------
# §3 tenancy, people, hierarchy
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
class Person(Base):
    display_name: str
    email: str
    kind: PersonKind = PersonKind.HUMAN
    time_zone: str = "UTC"
    is_active: bool = True


@dataclass(kw_only=True)
class Workspace(Base):
    name: str
    slug: str
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class WorkspaceMember(Base):
    workspace_id: str
    person_id: str
    handle: str
    capacity_hours_per_week: Optional[Decimal] = None
    cost_rate: Optional[Decimal] = None
    status: MemberStatus = MemberStatus.ACTIVE
    inbox_project_id: Optional[str] = None


@dataclass(kw_only=True)
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


@dataclass(kw_only=True)
class Membership(Base):
    team_id: str
    person_id: str
    role: MembershipRole = MembershipRole.MEMBER
    allocation_pct: Optional[int] = None
    joined_at: datetime = field(default_factory=utcnow)
    left_at: Optional[datetime] = None


@dataclass(kw_only=True)
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


@dataclass(kw_only=True)
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


# ---------------------------------------------------------------------------
# §4 dimensions
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
class Dimension(Base):
    workspace_id: str
    key: str
    name: str
    kind: DimensionKind = DimensionKind.CUSTOM
    value_type: ValueTypeShape = ValueTypeShape.FLAT
    cardinality: Cardinality = Cardinality.SINGLE
    applies_to: list[TargetType] = field(default_factory=lambda: [TargetType.WORK_ITEM])
    is_required_for: Optional[dict[str, Any]] = None
    owner_role: Optional[str] = None


@dataclass(kw_only=True)
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


@dataclass(kw_only=True)
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
    set_at: datetime = field(default_factory=utcnow)
    confidence: Optional[Decimal] = None
    is_confirmed: bool = True
    confirmed_by: Optional[str] = None


# ---------------------------------------------------------------------------
# §5 delivery process
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
class Process(Base):
    workspace_id: str
    key: str
    name: str
    description: Optional[str] = None
    applies_to: list[TargetType] = field(default_factory=lambda: [TargetType.WORK_ITEM])
    is_default: bool = False
    owner_id: Optional[str] = None


@dataclass(kw_only=True)
class Stage(Base):
    process_id: str
    key: str
    name: str
    position: int
    category: StageCategory
    maps_to_state_category: StateCategory          # required (v0.4 decision 1)
    color: Optional[str] = None
    entry_criteria: Optional[list[str]] = None
    exit_criteria: Optional[list[str]] = None
    expected_duration_days: Optional[int] = None
    wip_limit: Optional[int] = None
    requires_approval: bool = False


@dataclass(kw_only=True)
class StagePlacement(Base):
    target_type: TargetType
    target_id: str
    process_id: str
    stage_id: str
    entered_at: datetime = field(default_factory=utcnow)
    exited_at: Optional[datetime] = None
    moved_by: Optional[str] = None
    reason: Optional[str] = None
    approval_id: Optional[str] = None

    @property
    def is_current(self) -> bool:
        return self.exited_at is None


@dataclass(kw_only=True)
class StageTransitionRule(Base):
    process_id: str
    to_stage_id: str
    from_stage_id: Optional[str] = None
    allowed_roles: list[str] = field(default_factory=list)
    required_evidence_kinds: Optional[list[EvidenceKind]] = None
    auto_when: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------
# §6 products, objectives, weights
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
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


@dataclass(kw_only=True)
class ProductLink(Base):
    product_id: str
    target_type: TargetType
    target_id: str
    role: ProductLinkRole = ProductLinkRole.DELIVERS
    weight: Optional[Decimal] = None


@dataclass(kw_only=True)
class Objective(Base):
    workspace_id: str
    scope_type: ScopeType
    scope_id: str
    title: str
    period: str                       # e.g. "2026-Q4"
    owner_id: str
    status: str = "active"


@dataclass(kw_only=True)
class KeyResult(Base):
    objective_id: str
    title: str
    target_value: Decimal
    unit: str
    current_value: Decimal = Decimal(0)
    metric_key: Optional[str] = None
    source: str = "manual"            # manual | metric_snapshot


@dataclass(kw_only=True)
class WeightScheme(Base):
    workspace_id: str
    name: str
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: Optional[str] = None
    formula: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
class Weight(Base):
    work_item_id: str
    scheme_id: str
    value: Decimal
    source: ValueSource = ValueSource.FORMULA
    set_by: Optional[str] = None
    computed_at: datetime = field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# §7 projects
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
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
    settings: dict[str, Any] = field(default_factory=dict)
    external_refs: dict[str, str] = field(default_factory=dict)   # {"jira": "WEB"}


@dataclass(kw_only=True)
class ProjectMember(Base):
    project_id: str
    person_id: str
    role: ProjectRole = ProjectRole.CONTRIBUTOR
    allocation_pct: Optional[int] = None


@dataclass(kw_only=True)
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
    required_fields: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class Workflow(Base):
    workspace_id: str
    name: str
    is_default: bool = False


@dataclass(kw_only=True)
class WorkflowState(Base):
    workflow_id: str
    key: str
    name: str
    category: StateCategory
    position: int
    wip_limit: Optional[int] = None
    is_initial: bool = False
    is_terminal: bool = False


@dataclass(kw_only=True)
class Iteration(Base):
    project_id: str
    name: str
    start_date: date
    end_date: date
    goal: Optional[str] = None
    state: IterationState = IterationState.PLANNED
    committed_points: Optional[Decimal] = None
    completed_points: Optional[Decimal] = None


@dataclass(kw_only=True)
class Milestone(Base):
    project_id: str
    name: str
    due_date: date
    description: Optional[str] = None
    completed_at: Optional[datetime] = None
    kind: MilestoneKind = MilestoneKind.DEADLINE
    approval_id: Optional[str] = None


@dataclass(kw_only=True)
class Component(Base):
    project_id: str
    name: str
    lead_id: Optional[str] = None


@dataclass(kw_only=True)
class Release(Base):
    project_id: str
    name: str
    release_date: Optional[date] = None
    state: ReleaseState = ReleaseState.UNRELEASED


@dataclass(kw_only=True)
class Risk(Base):
    project_id: str
    key: str
    title: str
    owner_id: str
    target_type: TargetType = TargetType.PROJECT
    target_id: Optional[str] = None
    description: Optional[str] = None
    category: RiskCategory = RiskCategory.TECHNICAL
    probability: int = 3          # 1-5
    impact: int = 3               # 1-5
    status: RiskStatus = RiskStatus.IDENTIFIED
    trigger: Optional[str] = None
    response_strategy: RiskResponse = RiskResponse.MITIGATE
    review_date: Optional[date] = None

    @property
    def score(self) -> int:
        return self.probability * self.impact


@dataclass(kw_only=True)
class RiskLink(Base):
    risk_id: str
    work_item_id: str
    inherited: bool = False


@dataclass(kw_only=True)
class RiskAction(Base):
    risk_id: str
    description: str
    work_item_id: Optional[str] = None
    due_date: Optional[date] = None
    status: str = "open"


@dataclass(kw_only=True)
class Approval(Base):
    project_id: str
    target_type: TargetType
    target_id: str
    title: str
    requested_by: str
    policy: ApprovalPolicy = ApprovalPolicy.ANY_ONE
    quorum_n: Optional[int] = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = field(default_factory=utcnow)
    resolved_at: Optional[datetime] = None


@dataclass(kw_only=True)
class ApprovalDecision(Base):
    approval_id: str
    approver_id: str
    decision: Decision = Decision.PENDING
    comment: Optional[str] = None
    decided_at: Optional[datetime] = None


@dataclass(kw_only=True)
class RecurrenceRule(Base):
    project_id: str
    template_item_id: str
    rrule: str                                    # iCal RRULE
    time_zone: str = "UTC"
    assign_to: dict[str, Any] = field(default_factory=dict)
    lead_days: int = 0
    next_run_at: Optional[datetime] = None
    is_active: bool = True


# ---------------------------------------------------------------------------
# §7 work items
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
class WorkItem(Base):
    project_id: str
    key: str
    type_id: str
    title: str                                     # the only capture-layer required field
    state_id: str
    reporter_id: str
    parent_id: Optional[str] = None
    path: str = "/"
    description: Optional[str] = None
    priority: Priority = Priority.MEDIUM
    priority_source: ValueSource = ValueSource.MANUAL
    severity: Optional[str] = None
    assignee_id: Optional[str] = None
    team_id: Optional[str] = None
    iteration_id: Optional[str] = None
    milestone_id: Optional[str] = None
    release_id: Optional[str] = None
    component_id: Optional[str] = None
    planned_start: Optional[date] = None
    planned_end: Optional[date] = None
    due_date: Optional[date] = None
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    estimate_value: Optional[Decimal] = None
    estimate_unit: EstimationUnit = EstimationUnit.HOURS
    remaining_value: Optional[Decimal] = None
    weight: Decimal = Decimal("1.0")
    weight_scheme_id: Optional[str] = None
    impact: Optional[int] = None
    value_score: Optional[Decimal] = None
    progress_pct: int = 0
    progress_mode: ProgressMode = ProgressMode.MANUAL
    rank: str = ""
    is_provisional: bool = False
    is_template: bool = False
    template_item_id: Optional[str] = None
    visibility: Visibility = Visibility.INHERIT
    source: Optional[dict[str, Any]] = None
    external_refs: dict[str, str] = field(default_factory=dict)   # {"jira": "WEB-142"}
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


@dataclass(kw_only=True)
class WorkItemLink(Base):
    from_item_id: str
    to_item_id: str
    link_type: LinkType = LinkType.RELATES_TO
    lag_days: Optional[int] = None


@dataclass(kw_only=True)
class Assignment(Base):
    work_item_id: str
    person_id: str
    role: AssignmentRole = AssignmentRole.OWNER
    assigned_at: datetime = field(default_factory=utcnow)
    unassigned_at: Optional[datetime] = None


@dataclass(kw_only=True)
class Tag(Base):
    workspace_id: str
    name: str
    color: Optional[str] = None
    kind: TagKind = TagKind.LABEL
    is_provisional: bool = False


@dataclass(kw_only=True)
class TagLink(Base):
    tag_id: str
    target_type: TargetType
    target_id: str


@dataclass(kw_only=True)
class Estimate(Base):
    work_item_id: str
    person_id: str
    value: Decimal
    unit: EstimationUnit
    kind: EstimateKind = EstimateKind.ORIGINAL
    note: Optional[str] = None


@dataclass(kw_only=True)
class Checklist(Base):
    work_item_id: str
    title: str
    position: int = 0


@dataclass(kw_only=True)
class ChecklistItem(Base):
    checklist_id: str
    text: str
    position: int = 0
    is_done: bool = False
    done_by: Optional[str] = None
    done_at: Optional[datetime] = None


@dataclass(kw_only=True)
class Comment(Base):
    target_type: TargetType
    target_id: str
    author_id: str
    body: str
    parent_comment_id: Optional[str] = None
    edited_at: Optional[datetime] = None


@dataclass(kw_only=True)
class Attachment(Base):
    target_type: TargetType
    target_id: str
    uploader_id: str
    filename: str
    mime_type: str
    size_bytes: int
    storage_url: str
    kind: AttachmentKind = AttachmentKind.FILE


@dataclass(kw_only=True)
class CustomFieldDefinition(Base):
    workspace_id: str
    name: str
    key: str
    field_type: FieldType
    project_id: Optional[str] = None
    options: Optional[list[str]] = None
    applies_to_types: Optional[list[str]] = None
    is_required: bool = False


@dataclass(kw_only=True)
class CustomFieldValue(Base):
    work_item_id: str
    field_definition_id: str
    value_text: Optional[str] = None
    value_number: Optional[Decimal] = None
    value_date: Optional[date] = None
    value_json: Optional[Any] = None


@dataclass(kw_only=True)
class Baseline(Base):
    project_id: str
    work_item_id: str
    baseline_name: str
    captured_by: str
    planned_start: Optional[date] = None
    planned_end: Optional[date] = None
    estimate_value: Optional[Decimal] = None
    captured_at: datetime = field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# §7 capture layer
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
class Activity(Base):
    person_id: str
    work_item_id: str
    project_id: str
    kind: ActivityKind = ActivityKind.WORKING
    status: ActivityStatus = ActivityStatus.ACTIVE
    started_at: datetime = field(default_factory=utcnow)
    ended_at: Optional[datetime] = None
    note: Optional[str] = None
    client_meta: Optional[dict[str, Any]] = None
    mood: Optional[int] = None


@dataclass(kw_only=True)
class Evidence(Base):
    activity_id: str
    work_item_id: str
    person_id: str
    kind: EvidenceKind = EvidenceKind.NOTE
    title: Optional[str] = None
    body: Optional[str] = None
    url: Optional[str] = None
    attachment_id: Optional[str] = None
    captured_at: datetime = field(default_factory=utcnow)
    source: CaptureSource = CaptureSource.MANUAL


@dataclass(kw_only=True)
class TimeEntry(Base):
    person_id: str
    work_item_id: str
    date: date
    minutes: int
    activity_id: Optional[str] = None
    is_billable: bool = False
    note: Optional[str] = None
    source: CaptureSource = CaptureSource.TIMER


@dataclass(kw_only=True)
class DailyLog(Base):
    workspace_id: str
    person_id: str
    date: date
    summary: str = ""
    item_ids: list[str] = field(default_factory=list)
    minutes_total: int = 0
    blockers: Optional[str] = None
    status: DailyLogStatus = DailyLogStatus.DRAFT
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None


@dataclass(kw_only=True)
class Event(Base):
    workspace_id: str
    actor_id: str
    entity_type: TargetType
    entity_id: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: EventSource = EventSource.CLIENT
    occurred_at: datetime = field(default_factory=utcnow)
    received_at: datetime = field(default_factory=utcnow)
    client_id: Optional[str] = None
    raw_input: Optional[str] = None
    correlation_id: Optional[str] = None


# ---------------------------------------------------------------------------
# §8 governance
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
class PolicyTemplate(Base):
    key: str
    name: str
    category: PolicyCategory
    cel_source: str
    params: list[dict[str, Any]] = field(default_factory=list)   # [{name,type,default,min,max}]
    description: Optional[str] = None
    workspace_id: Optional[str] = None
    is_builtin: bool = False
    published_scope: str = "workspace"                          # workspace | shared


@dataclass(kw_only=True)
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
    schedule: Optional[str] = None                 # cron
    condition_template_id: Optional[str] = None
    template_params: Optional[dict[str, Any]] = None
    candidate_filter: Optional[dict[str, Any]] = None
    context_schema_version: int = 1
    severity: Severity = Severity.WARNING
    category: PolicyCategory = PolicyCategory.PROCESS
    notify: dict[str, Any] = field(default_factory=dict)
    auto_action: Optional[dict[str, Any]] = None
    grace_period: Optional[str] = None             # ISO-8601 duration
    is_active: bool = True


@dataclass(kw_only=True)
class PolicyTestRun(Base):
    policy_id: str
    window: str
    fired_count: int
    sample_target_ids: list[str] = field(default_factory=list)
    ran_at: datetime = field(default_factory=utcnow)


@dataclass(kw_only=True)
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
    detected_at: datetime = field(default_factory=utcnow)
    evidence: dict[str, Any] = field(default_factory=dict)
    status: ViolationStatus = ViolationStatus.OPEN
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolution_note: Optional[str] = None
    waived_by: Optional[str] = None
    waive_reason: Optional[str] = None


@dataclass(kw_only=True)
class HabitDefinition(Base):
    key: str
    name: str
    polarity: Polarity
    formula: dict[str, Any]
    thresholds: dict[str, Any] = field(default_factory=dict)
    category: PolicyCategory = PolicyCategory.PROCESS
    workspace_id: Optional[str] = None


@dataclass(kw_only=True)
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
    linked_violation_ids: list[str] = field(default_factory=list)
    status: HabitStatus = HabitStatus.OBSERVED
    coaching_note: Optional[str] = None
    category: PolicyCategory = PolicyCategory.PROCESS   # wellbeing → managers-only rule in ACL


@dataclass(kw_only=True)
class Insight(Base):
    scope_type: ScopeType
    scope_id: str
    period: str
    kind: InsightKind
    body: str
    supporting_ids: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=utcnow)
    status: InsightStatus = InsightStatus.NEW


@dataclass(kw_only=True)
class MetricSnapshot(Base):
    scope_type: ScopeType
    scope_id: str
    date: date
    weight_scheme_id: Optional[str] = None
    metrics: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# §9 views
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
class SavedView(Base):
    workspace_id: str
    owner_id: str
    name: str
    entity: TargetType = TargetType.WORK_ITEM
    scope_type: ScopeType = ScopeType.PERSON
    scope_id: Optional[str] = None
    filter: dict[str, Any] = field(default_factory=dict)
    group_by: list[str] = field(default_factory=list)
    sort_by: list[str] = field(default_factory=list)
    layout: ViewLayout = ViewLayout.LIST
    weight_scheme_id: Optional[str] = None
    is_shared: bool = False


# ---------------------------------------------------------------------------
# §10 access control
# ---------------------------------------------------------------------------

@dataclass(kw_only=True)
class Role(Base):
    key: str
    name: str
    permissions: list[str] = field(default_factory=list)
    workspace_id: Optional[str] = None


@dataclass(kw_only=True)
class RoleAssignment(Base):
    person_id: str
    role_id: str
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: Optional[str] = None


@dataclass(kw_only=True)
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


# ---------------------------------------------------------------------------
# §12 client message contract (what the local LLM emits, what the server accepts)
# ---------------------------------------------------------------------------

class ClientAction(StrEnum):
    START = "start"; PAUSE = "pause"; RESUME = "resume"; DONE = "done"; BLOCKED = "blocked"
    NOTE = "note"; EVIDENCE = "evidence"; ESTIMATE = "estimate"; PLAN = "plan"
    CREATE = "create"; UPDATE = "update"; LOG_TIME = "log_time"


@dataclass(kw_only=True)
class ClientTaskRef:
    """How a client refers to an item: by key, by exact id, or by free-text title (fuzzy-matched)."""
    key: Optional[str] = None
    id: Optional[str] = None
    title: Optional[str] = None


@dataclass(kw_only=True)
class ClientMessage:
    """One line from the reporter, after the local LLM has structured it.
    Everything except actor/action/at/client_id is optional."""
    actor: str                                   # "@handle"
    action: ClientAction
    at: datetime
    client_id: str                               # idempotency key
    item: Optional[ClientTaskRef] = None
    project: Optional[str] = None                # key / name / slug, resolved server-side
    tags: list[str] = field(default_factory=list)
    note: Optional[str] = None
    minutes: Optional[int] = None                # "took 2h"
    planned_end: Optional[datetime] = None       # "plan to finish tomorrow 5pm"
    estimate: Optional[dict[str, Any]] = None    # {"value": 3, "unit": "hours"}
    evidence: Optional[list[dict[str, Any]]] = None
    on_behalf_of: Optional[str] = None           # "for: @sara"
    # overlay hints the LLM may fill from the manager-published context pack:
    parent: Optional[ClientTaskRef] = None
    item_type: Optional[ItemTypeKey] = None
    priority: Optional[Priority] = None
    milestone: Optional[str] = None
    iteration: Optional[str] = None
    stage: Optional[str] = None
    product: Optional[str] = None
    risks: list[str] = field(default_factory=list)
    assignees: list[dict[str, str]] = field(default_factory=list)   # [{"person":"@x","role":"owner"}]
    custom_fields: dict[str, Any] = field(default_factory=dict)
    raw_input: Optional[str] = None
    llm_confidence: Optional[dict[str, float]] = None               # per-field confidence
    jira: Optional[dict[str, Any]] = None                           # {"project":"WEB","issuetype":"Task"} pass-through


@dataclass(kw_only=True)
class ClientBatch:
    """A client can send several messages at once (offline sync, 'doing 1,2,3')."""
    workspace: str
    messages: list[ClientMessage]
    client_version: str = "0.2"


# ---------------------------------------------------------------------------
# registry (for schema export / table generation)
# ---------------------------------------------------------------------------

ALL_MODELS: tuple[type, ...] = (
    Person, Workspace, WorkspaceMember, Team, Membership, ReportingLine, ContextNote,
    Dimension, DimensionValue, Classification,
    Process, Stage, StagePlacement, StageTransitionRule,
    Product, ProductLink, Objective, KeyResult, WeightScheme, Weight,
    Project, ProjectMember, WorkItemType, Workflow, WorkflowState, Iteration, Milestone,
    Component, Release, Risk, RiskLink, RiskAction, Approval, ApprovalDecision, RecurrenceRule,
    WorkItem, WorkItemLink, Assignment, Tag, TagLink, Estimate, Checklist, ChecklistItem,
    Comment, Attachment, CustomFieldDefinition, CustomFieldValue, Baseline,
    Activity, Evidence, TimeEntry, DailyLog, Event,
    PolicyTemplate, Policy, PolicyTestRun, Violation, HabitDefinition, Habit, Insight, MetricSnapshot,
    SavedView, Role, RoleAssignment, AccessGrant,
)


def to_json_schema(cls: type) -> dict[str, Any]:
    """Minimal JSON Schema export (enough for the LLM translator's structured-output mode).
    Swap for pydantic.TypeAdapter(cls).json_schema() when pydantic is adopted."""
    import dataclasses, typing
    hints = typing.get_type_hints(cls)
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in dataclasses.fields(cls):
        t = hints.get(f.name, f.type)
        schema: dict[str, Any]
        origin = typing.get_origin(t)
        args = typing.get_args(t)
        nullable = origin is typing.Union and type(None) in args
        if nullable:
            t = next(a for a in args if a is not type(None))
            origin = typing.get_origin(t)
        if isinstance(t, type) and issubclass(t, Enum):
            schema = {"type": "string", "enum": [e.value for e in t]}
        elif t in (str, "str"):
            schema = {"type": "string"}
        elif t in (int, "int"):
            schema = {"type": "integer"}
        elif t in (bool, "bool"):
            schema = {"type": "boolean"}
        elif t in (Decimal, float, "Decimal", "float"):
            schema = {"type": "number"}
        elif t in (date, datetime, "date", "datetime"):
            schema = {"type": "string", "format": "date-time"}
        elif origin in (list, tuple) or t in (list, tuple):
            inner = typing.get_args(t)
            item = inner[0] if inner else None
            if isinstance(item, type) and issubclass(item, Enum):
                schema = {"type": "array", "items": {"type": "string", "enum": [e.value for e in item]}}
            else:
                schema = {"type": "array"}
        elif origin is dict or t is dict:
            schema = {"type": "object"}
        elif dataclasses.is_dataclass(t):
            schema = to_json_schema(t)
        else:
            schema = {}
        if nullable:
            schema = {"anyOf": [schema, {"type": "null"}]}
        props[f.name] = schema
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            required.append(f.name)
    return {"title": cls.__name__, "type": "object", "properties": props, "required": required}


if __name__ == "__main__":
    import json
    print(json.dumps(to_json_schema(ClientMessage), indent=2)[:1500])
