"""§7 work items"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import Field

from tt_schema.base import Base, utcnow
from tt_schema.enums import *  # noqa: F403


class WorkItem(Base):
    project_id: str = Field(json_schema_extra={"layer": "system"})
    key: str = Field(json_schema_extra={"layer": "system"})
    type_id: str = Field(json_schema_extra={"layer": "system"})
    title: str = Field(json_schema_extra={"layer": "capture"})  # the only capture-layer required field
    state_id: str = Field(json_schema_extra={"layer": "overlay"})
    reporter_id: str = Field(json_schema_extra={"layer": "capture"})
    parent_id: Optional[str] = Field(default=None, json_schema_extra={"layer": "system"})
    path: str = Field(default="/", json_schema_extra={"layer": "system"})
    description: Optional[str] = Field(default=None, json_schema_extra={"layer": "overlay"})
    priority: Priority = Field(default=Priority.MEDIUM, json_schema_extra={"layer": "overlay"})
    priority_source: ValueSource = Field(default=ValueSource.MANUAL, json_schema_extra={"layer": "overlay"})
    severity: Optional[str] = Field(default=None, json_schema_extra={"layer": "overlay"})
    assignee_id: Optional[str] = Field(default=None, json_schema_extra={"layer": "overlay"})
    team_id: Optional[str] = Field(default=None, json_schema_extra={"layer": "overlay"})
    iteration_id: Optional[str] = Field(default=None, json_schema_extra={"layer": "overlay"})
    milestone_id: Optional[str] = Field(default=None, json_schema_extra={"layer": "overlay"})
    release_id: Optional[str] = Field(default=None, json_schema_extra={"layer": "overlay"})
    component_id: Optional[str] = Field(default=None, json_schema_extra={"layer": "overlay"})
    planned_start: Optional[date] = Field(default=None, json_schema_extra={"layer": "overlay"})
    planned_end: Optional[date] = Field(default=None, json_schema_extra={"layer": "either"})
    due_date: Optional[date] = Field(default=None, json_schema_extra={"layer": "overlay"})
    actual_start: Optional[datetime] = Field(default=None, json_schema_extra={"layer": "capture"})
    actual_end: Optional[datetime] = Field(default=None, json_schema_extra={"layer": "capture"})
    estimate_value: Optional[Decimal] = Field(default=None, json_schema_extra={"layer": "either"})
    estimate_unit: EstimationUnit = Field(default=EstimationUnit.HOURS, json_schema_extra={"layer": "either"})
    remaining_value: Optional[Decimal] = Field(default=None, json_schema_extra={"layer": "either"})
    weight: Decimal = Field(default=Decimal("1.0"), json_schema_extra={"layer": "overlay"})
    weight_scheme_id: Optional[str] = Field(default=None, json_schema_extra={"layer": "overlay"})
    impact: Optional[int] = Field(default=None, json_schema_extra={"layer": "overlay"})
    value_score: Optional[Decimal] = Field(default=None, json_schema_extra={"layer": "overlay"})
    progress_pct: int = Field(default=0, json_schema_extra={"layer": "either"})
    progress_mode: ProgressMode = Field(default=ProgressMode.MANUAL, json_schema_extra={"layer": "either"})
    rank: str = Field(default="", json_schema_extra={"layer": "system"})
    is_provisional: bool = Field(default=False, json_schema_extra={"layer": "system"})
    is_template: bool = Field(default=False, json_schema_extra={"layer": "system"})
    template_item_id: Optional[str] = Field(default=None, json_schema_extra={"layer": "system"})
    visibility: Visibility = Field(default=Visibility.INHERIT, json_schema_extra={"layer": "overlay"})
    source: Optional[dict[str, Any]] = Field(default=None, json_schema_extra={"layer": "overlay"})
    external_refs: dict[str, str] = Field(
        default_factory=dict, json_schema_extra={"layer": "system"}
    )  # {"jira": "WEB-142"}
    resolved_at: Optional[datetime] = Field(default=None, json_schema_extra={"layer": "system"})
    closed_at: Optional[datetime] = Field(default=None, json_schema_extra={"layer": "system"})


class WorkItemLink(Base):
    from_item_id: str
    to_item_id: str
    link_type: LinkType = LinkType.RELATES_TO
    lag_days: Optional[int] = None


class Assignment(Base):
    work_item_id: str
    person_id: str
    role: AssignmentRole = AssignmentRole.OWNER
    assigned_at: datetime = Field(default_factory=utcnow)
    unassigned_at: Optional[datetime] = None


class Tag(Base):
    workspace_id: str
    name: str
    color: Optional[str] = None
    kind: TagKind = TagKind.LABEL
    is_provisional: bool = False


class TagLink(Base):
    tag_id: str
    target_type: TargetType
    target_id: str


class Estimate(Base):
    work_item_id: str
    person_id: str
    value: Decimal
    unit: EstimationUnit
    kind: EstimateKind = EstimateKind.ORIGINAL
    note: Optional[str] = None


class Checklist(Base):
    work_item_id: str
    title: str
    position: int = 0


class ChecklistItem(Base):
    checklist_id: str
    text: str
    position: int = 0
    is_done: bool = False
    done_by: Optional[str] = None
    done_at: Optional[datetime] = None


class Comment(Base):
    target_type: TargetType
    target_id: str
    author_id: str
    body: str
    parent_comment_id: Optional[str] = None
    edited_at: Optional[datetime] = None


class Attachment(Base):
    target_type: TargetType
    target_id: str
    uploader_id: str
    filename: str
    mime_type: str
    size_bytes: int
    storage_url: str
    kind: AttachmentKind = AttachmentKind.FILE


class CustomFieldDefinition(Base):
    workspace_id: str
    name: str
    key: str
    field_type: FieldType
    project_id: Optional[str] = None
    options: Optional[list[str]] = None
    applies_to_types: Optional[list[str]] = None
    is_required: bool = False


class CustomFieldValue(Base):
    work_item_id: str
    field_definition_id: str
    value_text: Optional[str] = None
    value_number: Optional[Decimal] = None
    value_date: Optional[date] = None
    value_json: Optional[Any] = None


class Baseline(Base):
    project_id: str
    work_item_id: str
    baseline_name: str
    captured_by: str
    planned_start: Optional[date] = None
    planned_end: Optional[date] = None
    estimate_value: Optional[Decimal] = None
    captured_at: datetime = Field(default_factory=utcnow)


CAPTURE_FIELDS: frozenset[str] = frozenset(
    name
    for name, f in WorkItem.model_fields.items()
    if (f.json_schema_extra or {}).get("layer") == "capture"  # type: ignore[union-attr]
)
"""WorkItem columns written only by the capture layer; overlay repositories must never UPDATE them."""
