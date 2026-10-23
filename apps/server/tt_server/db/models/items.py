"""SQLAlchemy models for tt_schema (generated once, hand-owned afterwards). No business logic."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tt_schema.enums import (
    AssignmentRole,
    AttachmentKind,
    EstimateKind,
    EstimationUnit,
    FieldType,
    LinkType,
    Priority,
    ProgressMode,
    TagKind,
    TargetType,
    ValueSource,
    Visibility,
)

from tt_server.db.base import LTree, RowBase, WorkspaceScopedMixin, _values


class WorkItem(RowBase):
    __tablename__ = "work_items"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    type_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_item_types.id"), nullable=False)
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    state_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("workflow_states.id"), nullable=False)
    reporter_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=True)
    path: Mapped[str] = mapped_column(LTree(), nullable=False, server_default="")
    description: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    priority: Mapped[Priority] = mapped_column(
        sa.Enum(
            Priority,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=Priority.MEDIUM,
    )
    priority_source: Mapped[ValueSource] = mapped_column(
        sa.Enum(
            ValueSource,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ValueSource.MANUAL,
    )
    severity: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    assignee_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    team_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("teams.id"), nullable=True)
    iteration_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("iterations.id"), nullable=True)
    milestone_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("milestones.id"), nullable=True)
    release_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("releases.id"), nullable=True)
    component_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("components.id"), nullable=True)
    planned_start: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    planned_end: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    due_date: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    actual_start: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    actual_end: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    estimate_value: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)
    estimate_unit: Mapped[EstimationUnit] = mapped_column(
        sa.Enum(
            EstimationUnit,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=EstimationUnit.HOURS,
    )
    remaining_value: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)
    weight: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False, default=Decimal("1.0"))
    weight_scheme_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("weight_schemes.id"), nullable=True
    )
    impact: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)
    value_score: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)
    progress_pct: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    progress_mode: Mapped[ProgressMode] = mapped_column(
        sa.Enum(
            ProgressMode,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=ProgressMode.MANUAL,
    )
    rank: Mapped[str] = mapped_column(sa.Text(), nullable=False, default="")
    is_provisional: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    is_template: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    template_item_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("work_items.id"), nullable=True
    )
    visibility: Mapped[Visibility] = mapped_column(
        sa.Enum(
            Visibility,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=Visibility.INHERIT,
    )
    source: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    external_refs: Mapped[Any] = mapped_column(JSONB(), nullable=False, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    __table_args__ = (
        sa.UniqueConstraint("project_id", "key"),
        sa.Index(
            "ix_work_items_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        sa.Index("ix_work_items_path", "path", postgresql_using="gist"),
    )


class WorkItemLink(RowBase):
    __tablename__ = "work_item_links"
    from_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    to_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    link_type: Mapped[LinkType] = mapped_column(
        sa.Enum(
            LinkType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=LinkType.RELATES_TO,
    )
    lag_days: Mapped[int | None] = mapped_column(sa.Integer(), nullable=True)


class Assignment(RowBase):
    __tablename__ = "assignments"
    work_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    role: Mapped[AssignmentRole] = mapped_column(
        sa.Enum(
            AssignmentRole,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=AssignmentRole.OWNER,
    )
    assigned_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    unassigned_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class Tag(WorkspaceScopedMixin, RowBase):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    color: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    kind: Mapped[TagKind] = mapped_column(
        sa.Enum(
            TagKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=TagKind.LABEL,
    )
    is_provisional: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)


class TagLink(RowBase):
    __tablename__ = "tag_links"
    tag_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("tags.id"), nullable=False)
    target_type: Mapped[TargetType] = mapped_column(
        sa.Enum(
            TargetType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    target_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)


class Estimate(RowBase):
    __tablename__ = "estimates"
    work_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    person_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    value: Mapped[Decimal] = mapped_column(sa.Numeric(18, 4), nullable=False)
    unit: Mapped[EstimationUnit] = mapped_column(
        sa.Enum(
            EstimationUnit,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    kind: Mapped[EstimateKind] = mapped_column(
        sa.Enum(
            EstimateKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=EstimateKind.ORIGINAL,
    )
    note: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)


class Checklist(RowBase):
    __tablename__ = "checklists"
    work_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    title: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    position: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)


class ChecklistItem(RowBase):
    __tablename__ = "checklist_items"
    checklist_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("checklists.id"), nullable=False)
    text: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    position: Mapped[int] = mapped_column(sa.Integer(), nullable=False, default=0)
    is_done: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)
    done_by: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class Comment(RowBase):
    __tablename__ = "comments"
    target_type: Mapped[TargetType] = mapped_column(
        sa.Enum(
            TargetType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    target_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    author_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    parent_comment_id: Mapped[str | None] = mapped_column(
        sa.Text(), sa.ForeignKey("comments.id"), nullable=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)


class Attachment(RowBase):
    __tablename__ = "attachments"
    target_type: Mapped[TargetType] = mapped_column(
        sa.Enum(
            TargetType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    target_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    uploader_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    filename: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    mime_type: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    storage_url: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    kind: Mapped[AttachmentKind] = mapped_column(
        sa.Enum(
            AttachmentKind,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
        default=AttachmentKind.FILE,
    )


class CustomFieldDefinition(WorkspaceScopedMixin, RowBase):
    __tablename__ = "custom_field_definitions"
    name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    key: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    field_type: Mapped[FieldType] = mapped_column(
        sa.Enum(
            FieldType,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=False,
            values_callable=_values,
        ),
        nullable=False,
    )
    project_id: Mapped[str | None] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=True)
    options: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    applies_to_types: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)
    is_required: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, default=False)


class CustomFieldValue(RowBase):
    __tablename__ = "custom_field_values"
    work_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    field_definition_id: Mapped[str] = mapped_column(
        sa.Text(), sa.ForeignKey("custom_field_definitions.id"), nullable=False
    )
    value_text: Mapped[str | None] = mapped_column(sa.Text(), nullable=True)
    value_number: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)
    value_date: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    value_json: Mapped[Any | None] = mapped_column(JSONB(), nullable=True)


class Baseline(RowBase):
    __tablename__ = "baselines"
    project_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("projects.id"), nullable=False)
    work_item_id: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("work_items.id"), nullable=False)
    baseline_name: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    captured_by: Mapped[str] = mapped_column(sa.Text(), sa.ForeignKey("people.id"), nullable=False)
    planned_start: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    planned_end: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    estimate_value: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
