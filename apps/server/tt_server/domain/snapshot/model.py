"""The snapshot shape, field for field with the prototype's `D` object (ADR-0011: additive changes only)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Meta(_M):
    viewer: str
    scope_label: str
    generated_for: str


class Range(_M):
    from_: str = Field(alias="from")
    to: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Person(_M):
    handle: str
    name: str
    team: str | None


class Product(_M):
    key: str
    name: str


class Project(_M):
    key: str
    name: str
    product: str | None
    methodology: str
    team: str | None
    summary: str


class Epic(_M):
    key: str
    title: str
    project: str


class Milestone(_M):
    name: str
    project: str
    due: str


class Risk(_M):
    key: str
    title: str
    project: str
    p: int
    i: int
    status: str


class Item(_M):
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
    policy: str
    category: str
    severity: str
    target: str
    person: str | None
    team: str | None
    project: str | None
    detail: str


class Habit(_M):
    subject: str
    key: str
    polarity: str
    rate: float
    trend: str


class Now(_M):
    person: str
    item: str
    title: str
    project: str
    since_min: int
    kind: str


class ProcessInfo(_M):
    key: str
    stages: list[str]
    stage_expected_days: dict[str, int]


class Snapshot(_M):
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
    risks: list[Risk]
    items: list[Item]
    violations: list[Violation]
    habits: list[Habit]
    now: list[Now]


def json_schema() -> dict[str, Any]:
    return Snapshot.model_json_schema(by_alias=True)
