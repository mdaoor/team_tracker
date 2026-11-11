"""Enums ported 1:1 from docs/spec/team_tracker_models.py (names and values are normative)."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return str(self.value)


class PersonKind(StrEnum):
    HUMAN = "human"
    BOT = "bot"
    EXTERNAL = "external"


class MemberStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"


class TeamKind(StrEnum):
    ORG = "org"
    VIRTUAL = "virtual"
    AD_HOC = "ad_hoc"


class MembershipRole(StrEnum):
    LEAD = "lead"
    MEMBER = "member"
    VIEWER = "viewer"


class ReportingKind(StrEnum):
    LINE = "line"
    SENIOR = "senior"
    PROJECT = "project"
    FUNCTIONAL = "functional"
    MENTOR = "mentor"
    DOTTED = "dotted"


class ScopeType(StrEnum):
    NONE = "none"
    WORKSPACE = "workspace"
    PROJECT = "project"
    TEAM = "team"
    PERSON = "person"
    PRODUCT = "product"
    PROCESS = "process"
    ITERATION = "iteration"


class ContextKind(StrEnum):
    GOAL = "goal"
    OKR = "okr"
    GUIDELINE = "guideline"
    BACKGROUND = "background"
    DECISION = "decision"
    LINK = "link"
    ONBOARDING = "onboarding"
    OTHER = "other"


class Visibility(StrEnum):
    INHERIT = "inherit"
    PRIVATE = "private"
    ASSIGNEES = "assignees"
    MEMBERS = "members"
    TEAM = "team"
    PROJECT = "project"
    WORKSPACE = "workspace"
    SCOPE_MEMBERS = "scope_members"
    MANAGERS_ONLY = "managers_only"


class ProjectKind(StrEnum):
    STANDARD = "standard"
    INBOX = "inbox"
    TEMPLATE = "template"


class Methodology(StrEnum):
    AGILE = "agile"
    WATERFALL = "waterfall"
    KANBAN = "kanban"
    HYBRID = "hybrid"
    NONE = "none"


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class ProjectRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    CONTRIBUTOR = "contributor"
    STAKEHOLDER = "stakeholder"
    VIEWER = "viewer"


class ItemTypeKey(StrEnum):
    EPIC = "epic"
    STORY = "story"
    TASK = "task"
    BUG = "bug"
    SUBTASK = "subtask"
    SPIKE = "spike"
    PHASE = "phase"
    DELIVERABLE = "deliverable"
    DECISION = "decision"


class EstimationUnit(StrEnum):
    POINTS = "points"
    HOURS = "hours"
    DAYS = "days"
    NONE = "none"


class StateCategory(StrEnum):
    OPEN = "open"
    ACTIVE = "active"
    DONE = "done"


class IterationState(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    CLOSED = "closed"


class MilestoneKind(StrEnum):
    GATE = "gate"
    RELEASE = "release"
    DEMO = "demo"
    DEADLINE = "deadline"
    OTHER = "other"


class ReleaseState(StrEnum):
    UNRELEASED = "unreleased"
    RELEASED = "released"
    ARCHIVED = "archived"


class Priority(StrEnum):
    LOWEST = "lowest"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    HIGHEST = "highest"
    CRITICAL = "critical"


class ValueSource(StrEnum):
    MANUAL = "manual"
    INHERITED = "inherited"
    RULE = "rule"
    IMPORT = "import"
    FORMULA = "formula"


class ProgressMode(StrEnum):
    MANUAL = "manual"
    CHILDREN = "children"
    CHECKLIST = "checklist"
    TIME = "time"


class LinkType(StrEnum):
    BLOCKS = "blocks"
    IS_BLOCKED_BY = "is_blocked_by"
    RELATES_TO = "relates_to"
    DUPLICATES = "duplicates"
    CLONES = "clones"
    DEPENDS_ON = "depends_on"
    FINISH_TO_START = "finish_to_start"
    START_TO_START = "start_to_start"
    FINISH_TO_FINISH = "finish_to_finish"


class AssignmentRole(StrEnum):
    OWNER = "owner"
    CONTRIBUTOR = "contributor"
    REVIEWER = "reviewer"
    APPROVER = "approver"
    WATCHER = "watcher"


class TagKind(StrEnum):
    LABEL = "label"
    SKILL = "skill"
    AREA = "area"
    CLIENT = "client"
    CUSTOM = "custom"


class EstimateKind(StrEnum):
    ORIGINAL = "original"
    REVISED = "revised"
    REMAINING = "remaining"


class TargetType(StrEnum):
    WORK_ITEM = "work_item"
    PROJECT = "project"
    PRODUCT = "product"
    ACTIVITY = "activity"
    PERSON = "person"
    RISK = "risk"
    MILESTONE = "milestone"
    RELEASE = "release"
    BASELINE = "baseline"
    CONTEXT_NOTE = "context_note"
    COMMENT = "comment"
    DAILY_LOG = "daily_log"
    SAVED_VIEW = "saved_view"
    STAGE_PLACEMENT = "stage_placement"
    TEAM = "team"
    OBJECTIVE = "objective"


class AttachmentKind(StrEnum):
    FILE = "file"
    LINK = "link"
    IMAGE = "image"


class FieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    PERSON = "person"
    URL = "url"
    BOOL = "bool"


class RiskCategory(StrEnum):
    SCHEDULE = "schedule"
    SCOPE = "scope"
    BUDGET = "budget"
    TECHNICAL = "technical"
    RESOURCE = "resource"
    EXTERNAL = "external"
    COMPLIANCE = "compliance"


class RiskStatus(StrEnum):
    IDENTIFIED = "identified"
    ANALYSING = "analysing"
    MITIGATING = "mitigating"
    MONITORING = "monitoring"
    OCCURRED = "occurred"
    CLOSED = "closed"


class RiskResponse(StrEnum):
    AVOID = "avoid"
    MITIGATE = "mitigate"
    TRANSFER = "transfer"
    ACCEPT = "accept"


class ApprovalPolicy(StrEnum):
    ANY_ONE = "any_one"
    ALL = "all"
    QUORUM = "quorum"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class Decision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ABSTAINED = "abstained"


class ActivityKind(StrEnum):
    WORKING = "working"
    REVIEWING = "reviewing"
    MEETING = "meeting"
    BLOCKED = "blocked"
    RESEARCHING = "researching"
    WAITING = "waiting"
    OTHER = "other"


class ActivityStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"


class EvidenceKind(StrEnum):
    NOTE = "note"
    SCREENSHOT = "screenshot"
    LINK = "link"
    COMMIT = "commit"
    PR = "pr"
    DOCUMENT = "document"
    FILE = "file"
    METRIC = "metric"


class CaptureSource(StrEnum):
    MANUAL = "manual"
    CLIENT_AUTO = "client_auto"
    INTEGRATION = "integration"
    TIMER = "timer"
    IMPORTED = "imported"


class DailyLogStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    REVIEWED = "reviewed"


class EventSource(StrEnum):
    CLIENT = "client"
    OVERLAY = "overlay"
    PROPAGATION = "propagation"
    RULE = "rule"
    IMPORT = "import"


class DimensionKind(StrEnum):
    BUILTIN = "builtin"
    CUSTOM = "custom"


class ValueTypeShape(StrEnum):
    TREE = "tree"
    FLAT = "flat"


class Cardinality(StrEnum):
    SINGLE = "single"
    MULTI = "multi"


class StageCategory(StrEnum):
    IDEATION = "ideation"
    PLANNING = "planning"
    EXECUTION = "execution"
    VALIDATION = "validation"
    RELEASE = "release"
    OPERATE = "operate"
    CLOSED = "closed"


class ProductStatus(StrEnum):
    CONCEPT = "concept"
    ACTIVE = "active"
    SUNSET = "sunset"
    RETIRED = "retired"


class ProductLinkRole(StrEnum):
    DELIVERS = "delivers"
    SUPPORTS = "supports"
    DEPENDS_ON = "depends_on"
    OWNS = "owns"


class PolicyTrigger(StrEnum):
    ON_EVENT = "on_event"
    SCHEDULED = "scheduled"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    VIOLATION = "violation"
    CRITICAL = "critical"


class PolicyCategory(StrEnum):
    PROCESS = "process"
    QUALITY = "quality"
    TIME = "time"
    PLANNING = "planning"
    COLLABORATION = "collaboration"
    COMPLIANCE = "compliance"
    WELLBEING = "wellbeing"


class ViolationStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    WAIVED = "waived"
    FALSE_POSITIVE = "false_positive"


class Polarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class Trend(StrEnum):
    IMPROVING = "improving"
    STABLE = "stable"
    WORSENING = "worsening"


class HabitStatus(StrEnum):
    OBSERVED = "observed"
    FLAGGED = "flagged"
    IN_COACHING = "in_coaching"
    RESOLVED = "resolved"


class ViewLayout(StrEnum):
    LIST = "list"
    BOARD = "board"
    GANTT = "gantt"
    CALENDAR = "calendar"
    TIMELINE = "timeline"
    TABLE = "table"
    PIPELINE = "pipeline"
    MATRIX = "matrix"


class GrantLevel(StrEnum):
    READ = "read"
    COMMENT = "comment"
    WRITE = "write"
    ADMIN = "admin"


class SubjectType(StrEnum):
    PERSON = "person"
    TEAM = "team"
    ROLE = "role"


class InsightKind(StrEnum):
    SUMMARY = "summary"
    ANOMALY = "anomaly"
    RECOMMENDATION = "recommendation"


class InsightStatus(StrEnum):
    NEW = "new"
    REVIEWED = "reviewed"
    ACTIONED = "actioned"
    DISMISSED = "dismissed"
