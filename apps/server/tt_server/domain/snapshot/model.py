"""The snapshot shape, field for field with the prototype's `D` object (ADR-0011: additive changes only)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Meta(_M):
    """Provenance block of a snapshot: who it was built for and how."""

    viewer: str
    scope_label: str
    generated_for: str


class Range(_M):
    """The inclusive date range a snapshot covers."""

    from_: str = Field(alias="from")
    to: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Person(_M):
    """A person as seen in a snapshot."""

    handle: str
    name: str
    team: str | None


class Product(_M):
    """A product as seen in a snapshot."""

    key: str
    name: str


class Project(_M):
    """A project as seen in a snapshot."""

    key: str
    name: str
    product: str | None
    methodology: str
    team: str | None
    summary: str
    owner: str | None = None
    planned_start: str | None = None
    planned_end: str | None = None
    status: str | None = None  # not_started | in_progress, derived from the plan (None: no plan)
    health: str | None = None  # green | amber | red | unknown
    baseline_name: str | None = None


class Epic(_M):
    """An epic as seen in a snapshot."""

    key: str
    title: str
    project: str


class Milestone(_M):
    """A plan milestone with its computed status."""

    name: str
    project: str
    due: str
    kind: str = "other"
    status: str = "planned"  # completed | overdue | due_soon | planned
    completed_at: str | None = None
    deliverables_done: int = 0
    deliverables_total: int = 0


class Phase(_M):
    """A plan phase with planned dates."""

    key: str
    title: str
    project: str
    planned_start: str | None = None
    planned_end: str | None = None
    status: str
    progress_pct: int | None = None
    deliverable_count: int = 0


class Deliverable(_M):
    """A plan deliverable with owner and status."""

    key: str
    title: str
    project: str
    phase: str | None = None
    owner: str | None = None
    planned_start: str | None = None
    planned_end: str | None = None
    baseline_end: str | None = None
    baseline_variance_days: int | None = None
    actual_end: str | None = None
    status: str
    progress_pct: int | None = None
    linked_tasks: int = 0
    hours_logged: float = 0
    depends_on: list[str] = []
    milestone: str | None = None


class Risk(_M):
    """A risk with probability and impact scores."""

    key: str
    title: str
    project: str
    p: int
    i: int
    status: str


class Item(_M):
    """A work item as seen in a snapshot."""

    key: str
    title: str
    project: str
    product: str | None
    epic: str | None
    type: str | None
    assignee: str | None
    team: str | None
    stage: str | None
    stage_age_days: int | None
    state: str
    priority: str
    weight: float
    estimate_h: float | None
    hours_logged: float
    planned_start: str | None
    planned_end: str | None
    actual_end: str | None
    tags: list[str]
    risks: list[str]
    last_touched: str | None
    evidence_count: int


class Violation(_M):
    """An open policy violation as seen in a snapshot."""

    policy: str
    category: str
    severity: str
    target: str
    person: str | None
    team: str | None
    project: str | None
    detail: str


class Habit(_M):
    """A computed habit rate and trend for a person or team."""

    subject: str
    key: str
    polarity: str
    rate: float
    trend: str


class Now(_M):
    """What a person is working on right now."""

    person: str
    item: str
    title: str
    project: str
    since_min: int
    kind: str


class ProcessInfo(_M):
    """A process definition with its stages and expected stage durations."""

    key: str
    stages: list[str]
    stage_expected_days: dict[str, int]


class Snapshot(_M):
    """The top-level snapshot document, versioned by SCHEMA_VERSION."""

    schema_version: int = SCHEMA_VERSION
    meta: Meta
    as_of: str
    workspace: str
    range: Range
    weight_scheme: str
    stages: list[str]
    stage_expected_days: dict[str, int]
    processes: list[ProcessInfo] | None = None
    people: list[Person]
    teams: list[str]
    products: list[Product]
    projects: list[Project]
    epics: list[Epic]
    milestones: list[Milestone]
    phases: list[Phase] = []
    deliverables: list[Deliverable] = []
    risks: list[Risk]
    items: list[Item]
    violations: list[Violation]
    habits: list[Habit]
    now: list[Now]


def json_schema() -> dict[str, Any]:
    """JSON Schema of the snapshot document (by alias), published for consumers."""
    return Snapshot.model_json_schema(by_alias=True)
