"""initial

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType


class LTree(UserDefinedType):
    """Frozen copy of tt_server.db.base.LTree so this migration never depends on app code."""

    cache_ok = True

    def get_col_spec(self, **kw):
        return "ltree"


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "insights",
        sa.Column(
            "scope_type",
            sa.Enum(
                "none",
                "workspace",
                "project",
                "team",
                "person",
                "product",
                "process",
                "iteration",
                name="scopetype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("summary", "anomaly", "recommendation", name="insightkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("supporting_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "new", "reviewed", "actioned", "dismissed", name="insightstatus", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_insights")),
    )
    op.create_table(
        "people",
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("human", "bot", "external", name="personkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("time_zone", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_people")),
        sa.UniqueConstraint("email", name=op.f("uq_people_email")),
    )
    op.create_table(
        "workspaces",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("slug", name=op.f("uq_workspaces_slug")),
    )
    op.create_table(
        "access_grants",
        sa.Column(
            "subject_type",
            sa.Enum("person", "team", "role", name="subjecttype", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column(
            "object_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("object_id", sa.Text(), nullable=False),
        sa.Column(
            "level",
            sa.Enum("read", "comment", "write", "admin", name="grantlevel", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("inherit_to_children", sa.Boolean(), nullable=False),
        sa.Column("granted_by", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["people.id"], name=op.f("fk_access_grants_granted_by_people")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_access_grants_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_grants")),
    )
    op.create_index(op.f("ix_access_grants_workspace_id"), "access_grants", ["workspace_id"], unique=False)
    op.create_table(
        "attachments",
        sa.Column(
            "target_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("uploader_id", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_url", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("file", "link", "image", name="attachmentkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["uploader_id"], ["people.id"], name=op.f("fk_attachments_uploader_id_people")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attachments")),
    )
    op.create_table(
        "comments",
        sa.Column(
            "target_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("parent_comment_id", sa.Text(), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["people.id"], name=op.f("fk_comments_author_id_people")),
        sa.ForeignKeyConstraint(
            ["parent_comment_id"], ["comments.id"], name=op.f("fk_comments_parent_comment_id_comments")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    op.create_table(
        "context_notes",
        sa.Column(
            "scope_type",
            sa.Enum(
                "none",
                "workspace",
                "project",
                "team",
                "person",
                "product",
                "process",
                "iteration",
                name="scopetype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("author_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "goal",
                "okr",
                "guideline",
                "background",
                "decision",
                "link",
                "onboarding",
                "other",
                name="contextkind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column(
            "visibility",
            sa.Enum(
                "inherit",
                "private",
                "assignees",
                "members",
                "team",
                "project",
                "workspace",
                "scope_members",
                "managers_only",
                name="visibility",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["people.id"], name=op.f("fk_context_notes_author_id_people")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_context_notes_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_context_notes")),
    )
    op.create_index(op.f("ix_context_notes_workspace_id"), "context_notes", ["workspace_id"], unique=False)
    op.create_table(
        "daily_logs",
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("item_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("minutes_total", sa.Integer(), nullable=False),
        sa.Column("blockers", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("draft", "confirmed", "reviewed", name="dailylogstatus", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name=op.f("fk_daily_logs_person_id_people")),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["people.id"], name=op.f("fk_daily_logs_reviewed_by_people")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_daily_logs_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_logs")),
    )
    op.create_index(op.f("ix_daily_logs_workspace_id"), "daily_logs", ["workspace_id"], unique=False)
    op.create_table(
        "dimensions",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("builtin", "custom", name="dimensionkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "value_type",
            sa.Enum("tree", "flat", name="valuetypeshape", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "cardinality",
            sa.Enum("single", "multi", name="cardinality", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("applies_to", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_required_for", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("owner_role", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_dimensions_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dimensions")),
        sa.UniqueConstraint("workspace_id", "key", name=op.f("uq_dimensions_workspace_id_key")),
    )
    op.create_index(op.f("ix_dimensions_workspace_id"), "dimensions", ["workspace_id"], unique=False)
    op.create_table(
        "events",
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column(
            "entity_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "client",
                "overlay",
                "propagation",
                "rule",
                "import",
                name="eventsource",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=True),
        sa.Column("raw_input", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["people.id"], name=op.f("fk_events_actor_id_people")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_events_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    op.create_index("ix_events_entity", "events", ["entity_type", "entity_id"], unique=False)
    op.create_index(op.f("ix_events_workspace_id"), "events", ["workspace_id"], unique=False)
    op.create_index(
        "ix_events_workspace_occurred",
        "events",
        ["workspace_id", sa.literal_column("occurred_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_events_client_id",
        "events",
        ["client_id"],
        unique=True,
        postgresql_where=sa.text("client_id IS NOT NULL"),
    )
    op.create_table(
        "habit_definitions",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "polarity",
            sa.Enum("positive", "negative", name="polarity", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("formula", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("thresholds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "process",
                "quality",
                "time",
                "planning",
                "collaboration",
                "compliance",
                "wellbeing",
                name="policycategory",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.Text(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_habit_definitions_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_habit_definitions")),
    )
    op.create_table(
        "habits",
        sa.Column(
            "subject_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "polarity",
            sa.Enum("positive", "negative", name="polarity", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("window", sa.Text(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("rate", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column(
            "trend",
            sa.Enum("improving", "stable", "worsening", name="trend", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("score", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_violation_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "observed",
                "flagged",
                "in_coaching",
                "resolved",
                name="habitstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("coaching_note", sa.Text(), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                "process",
                "quality",
                "time",
                "planning",
                "collaboration",
                "compliance",
                "wellbeing",
                name="policycategory",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_habits_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_habits")),
    )
    op.create_index(op.f("ix_habits_workspace_id"), "habits", ["workspace_id"], unique=False)
    op.create_table(
        "objectives",
        sa.Column(
            "scope_type",
            sa.Enum(
                "none",
                "workspace",
                "project",
                "team",
                "person",
                "product",
                "process",
                "iteration",
                name="scopetype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["people.id"], name=op.f("fk_objectives_owner_id_people")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_objectives_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_objectives")),
    )
    op.create_index(op.f("ix_objectives_workspace_id"), "objectives", ["workspace_id"], unique=False)
    op.create_table(
        "policies",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "target",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("condition_cel", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "scope_type",
            sa.Enum(
                "none",
                "workspace",
                "project",
                "team",
                "person",
                "product",
                "process",
                "iteration",
                name="scopetype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=True),
        sa.Column(
            "trigger",
            sa.Enum("on_event", "scheduled", name="policytrigger", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("event_types", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("schedule", sa.Text(), nullable=True),
        sa.Column("condition_template_id", sa.Text(), nullable=True),
        sa.Column("template_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("candidate_filter", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("context_schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "info", "warning", "violation", "critical", name="severity", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Enum(
                "process",
                "quality",
                "time",
                "planning",
                "collaboration",
                "compliance",
                "wellbeing",
                name="policycategory",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("notify", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("auto_action", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("grace_period", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["people.id"], name=op.f("fk_policies_owner_id_people")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_policies_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policies")),
    )
    op.create_index(op.f("ix_policies_workspace_id"), "policies", ["workspace_id"], unique=False)
    op.create_table(
        "policy_templates",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "process",
                "quality",
                "time",
                "planning",
                "collaboration",
                "compliance",
                "wellbeing",
                name="policycategory",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("cel_source", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.Column("published_scope", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_policy_templates_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_templates")),
    )
    op.create_table(
        "processes",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("applies_to", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["people.id"], name=op.f("fk_processes_owner_id_people")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_processes_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_processes")),
        sa.UniqueConstraint("workspace_id", "key", name=op.f("uq_processes_workspace_id_key")),
    )
    op.create_index(op.f("ix_processes_workspace_id"), "processes", ["workspace_id"], unique=False)
    op.create_table(
        "reporting_lines",
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("manager_id", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "line",
                "senior",
                "project",
                "functional",
                "mentor",
                "dotted",
                name="reportingkind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "scope_type",
            sa.Enum(
                "none",
                "workspace",
                "project",
                "team",
                "person",
                "product",
                "process",
                "iteration",
                name="scopetype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["manager_id"], ["people.id"], name=op.f("fk_reporting_lines_manager_id_people")
        ),
        sa.ForeignKeyConstraint(
            ["person_id"], ["people.id"], name=op.f("fk_reporting_lines_person_id_people")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_reporting_lines_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reporting_lines")),
    )
    op.create_index(
        op.f("ix_reporting_lines_workspace_id"), "reporting_lines", ["workspace_id"], unique=False
    )
    op.create_table(
        "roles",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_roles_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
    )
    op.create_table(
        "tags",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "label", "skill", "area", "client", "custom", name="tagkind", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("is_provisional", sa.Boolean(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_tags_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
    )
    op.create_index(op.f("ix_tags_workspace_id"), "tags", ["workspace_id"], unique=False)
    op.create_table(
        "teams",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("org", "virtual", "ad_hoc", name="teamkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("parent_team_id", sa.Text(), nullable=True),
        sa.Column("lead_id", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["lead_id"], ["people.id"], name=op.f("fk_teams_lead_id_people")),
        sa.ForeignKeyConstraint(["parent_team_id"], ["teams.id"], name=op.f("fk_teams_parent_team_id_teams")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_teams_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("workspace_id", "slug", name=op.f("uq_teams_workspace_id_slug")),
    )
    op.create_index(op.f("ix_teams_workspace_id"), "teams", ["workspace_id"], unique=False)
    op.create_table(
        "weight_schemes",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "scope_type",
            sa.Enum(
                "none",
                "workspace",
                "project",
                "team",
                "person",
                "product",
                "process",
                "iteration",
                name="scopetype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=True),
        sa.Column("formula", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_weight_schemes_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weight_schemes")),
    )
    op.create_index(op.f("ix_weight_schemes_workspace_id"), "weight_schemes", ["workspace_id"], unique=False)
    op.create_table(
        "workflows",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_workflows_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflows")),
    )
    op.create_index(op.f("ix_workflows_workspace_id"), "workflows", ["workspace_id"], unique=False)
    op.create_table(
        "dimension_values",
        sa.Column("dimension_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("parent_value_id", sa.Text(), nullable=True),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column(
            "backing_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("backing_id", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["dimension_id"], ["dimensions.id"], name=op.f("fk_dimension_values_dimension_id_dimensions")
        ),
        sa.ForeignKeyConstraint(
            ["parent_value_id"],
            ["dimension_values.id"],
            name=op.f("fk_dimension_values_parent_value_id_dimension_values"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dimension_values")),
        sa.UniqueConstraint("dimension_id", "key", name=op.f("uq_dimension_values_dimension_id_key")),
    )
    op.create_table(
        "key_results",
        sa.Column("objective_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("target_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("current_value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("metric_key", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["objective_id"], ["objectives.id"], name=op.f("fk_key_results_objective_id_objectives")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_key_results")),
    )
    op.create_table(
        "memberships",
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("lead", "member", "viewer", name="membershiprole", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("allocation_pct", sa.Integer(), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name=op.f("fk_memberships_person_id_people")),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_memberships_team_id_teams")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
    )
    op.create_table(
        "metric_snapshots",
        sa.Column(
            "scope_type",
            sa.Enum(
                "none",
                "workspace",
                "project",
                "team",
                "person",
                "product",
                "process",
                "iteration",
                name="scopetype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("weight_scheme_id", sa.Text(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["weight_scheme_id"],
            ["weight_schemes.id"],
            name=op.f("fk_metric_snapshots_weight_scheme_id_weight_schemes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_snapshots")),
    )
    op.create_table(
        "policy_test_runs",
        sa.Column("policy_id", sa.Text(), nullable=False),
        sa.Column("window", sa.Text(), nullable=False),
        sa.Column("fired_count", sa.Integer(), nullable=False),
        sa.Column("sample_target_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["policies.id"], name=op.f("fk_policy_test_runs_policy_id_policies")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_policy_test_runs")),
    )
    op.create_table(
        "products",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("parent_product_id", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "concept", "active", "sunset", "retired", name="productstatus", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("default_process_id", sa.Text(), nullable=True),
        sa.Column(
            "visibility",
            sa.Enum(
                "inherit",
                "private",
                "assignees",
                "members",
                "team",
                "project",
                "workspace",
                "scope_members",
                "managers_only",
                name="visibility",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["default_process_id"], ["processes.id"], name=op.f("fk_products_default_process_id_processes")
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["people.id"], name=op.f("fk_products_owner_id_people")),
        sa.ForeignKeyConstraint(
            ["parent_product_id"], ["products.id"], name=op.f("fk_products_parent_product_id_products")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_products_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("workspace_id", "key", name=op.f("uq_products_workspace_id_key")),
    )
    op.create_index(op.f("ix_products_workspace_id"), "products", ["workspace_id"], unique=False)
    op.create_table(
        "projects",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("default_item_type_id", sa.Text(), nullable=False),
        sa.Column("parent_project_id", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "kind",
            sa.Enum("standard", "inbox", "template", name="projectkind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "methodology",
            sa.Enum(
                "agile",
                "waterfall",
                "kanban",
                "hybrid",
                "none",
                name="methodology",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "active",
                "on_hold",
                "completed",
                "cancelled",
                "archived",
                name="projectstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("team_id", sa.Text(), nullable=True),
        sa.Column("default_process_id", sa.Text(), nullable=True),
        sa.Column("planned_start", sa.Date(), nullable=True),
        sa.Column("planned_end", sa.Date(), nullable=True),
        sa.Column("actual_start", sa.Date(), nullable=True),
        sa.Column("actual_end", sa.Date(), nullable=True),
        sa.Column(
            "visibility",
            sa.Enum(
                "inherit",
                "private",
                "assignees",
                "members",
                "team",
                "project",
                "workspace",
                "scope_members",
                "managers_only",
                name="visibility",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("is_provisional", sa.Boolean(), nullable=False),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("external_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["default_process_id"], ["processes.id"], name=op.f("fk_projects_default_process_id_processes")
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["people.id"], name=op.f("fk_projects_owner_id_people")),
        sa.ForeignKeyConstraint(
            ["parent_project_id"], ["projects.id"], name=op.f("fk_projects_parent_project_id_projects")
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_projects_team_id_teams")),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], name=op.f("fk_projects_workflow_id_workflows")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_projects_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("workspace_id", "key", name=op.f("uq_projects_workspace_id_key")),
    )
    op.create_index(op.f("ix_projects_workspace_id"), "projects", ["workspace_id"], unique=False)
    op.create_table(
        "role_assignments",
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("role_id", sa.Text(), nullable=False),
        sa.Column(
            "scope_type",
            sa.Enum(
                "none",
                "workspace",
                "project",
                "team",
                "person",
                "product",
                "process",
                "iteration",
                name="scopetype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["person_id"], ["people.id"], name=op.f("fk_role_assignments_person_id_people")
        ),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name=op.f("fk_role_assignments_role_id_roles")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_assignments")),
    )
    op.create_table(
        "saved_views",
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "entity",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "scope_type",
            sa.Enum(
                "none",
                "workspace",
                "project",
                "team",
                "person",
                "product",
                "process",
                "iteration",
                name="scopetype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("scope_id", sa.Text(), nullable=True),
        sa.Column("filter", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("group_by", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sort_by", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "layout",
            sa.Enum(
                "list",
                "board",
                "gantt",
                "calendar",
                "timeline",
                "table",
                "pipeline",
                "matrix",
                name="viewlayout",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("weight_scheme_id", sa.Text(), nullable=True),
        sa.Column("is_shared", sa.Boolean(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["people.id"], name=op.f("fk_saved_views_owner_id_people")),
        sa.ForeignKeyConstraint(
            ["weight_scheme_id"],
            ["weight_schemes.id"],
            name=op.f("fk_saved_views_weight_scheme_id_weight_schemes"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_saved_views_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_views")),
    )
    op.create_index(op.f("ix_saved_views_workspace_id"), "saved_views", ["workspace_id"], unique=False)
    op.create_table(
        "stages",
        sa.Column("process_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "category",
            sa.Enum(
                "ideation",
                "planning",
                "execution",
                "validation",
                "release",
                "operate",
                "closed",
                name="stagecategory",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "maps_to_state_category",
            sa.Enum("open", "active", "done", name="statecategory", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column("entry_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("exit_criteria", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expected_duration_days", sa.Integer(), nullable=True),
        sa.Column("wip_limit", sa.Integer(), nullable=True),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["process_id"], ["processes.id"], name=op.f("fk_stages_process_id_processes")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stages")),
        sa.UniqueConstraint("process_id", "key", name=op.f("uq_stages_process_id_key")),
    )
    op.create_table(
        "tag_links",
        sa.Column("tag_id", sa.Text(), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], name=op.f("fk_tag_links_tag_id_tags")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tag_links")),
    )
    op.create_table(
        "work_item_types",
        sa.Column(
            "key",
            sa.Enum(
                "epic",
                "story",
                "task",
                "bug",
                "subtask",
                "spike",
                "phase",
                "deliverable",
                "decision",
                name="itemtypekey",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("icon", sa.Text(), nullable=True),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column(
            "estimation_unit",
            sa.Enum("points", "hours", "days", "none", name="estimationunit", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("default_workflow_id", sa.Text(), nullable=True),
        sa.Column("required_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["default_workflow_id"],
            ["workflows.id"],
            name=op.f("fk_work_item_types_default_workflow_id_workflows"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_work_item_types_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_item_types")),
    )
    op.create_index(
        op.f("ix_work_item_types_workspace_id"), "work_item_types", ["workspace_id"], unique=False
    )
    op.create_table(
        "workflow_states",
        sa.Column("workflow_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "category",
            sa.Enum("open", "active", "done", name="statecategory", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("wip_limit", sa.Integer(), nullable=True),
        sa.Column("is_initial", sa.Boolean(), nullable=False),
        sa.Column("is_terminal", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], name=op.f("fk_workflow_states_workflow_id_workflows")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_states")),
    )
    op.create_table(
        "approvals",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column(
            "policy",
            sa.Enum("any_one", "all", "quorum", name="approvalpolicy", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("quorum_n", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "cancelled",
                name="approvalstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_approvals_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["people.id"], name=op.f("fk_approvals_requested_by_people")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    op.create_table(
        "classifications",
        sa.Column(
            "target_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("dimension_id", sa.Text(), nullable=False),
        sa.Column("value_id", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "manual",
                "inherited",
                "rule",
                "import",
                "formula",
                name="valuesource",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "inherited_from_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("inherited_from_id", sa.Text(), nullable=True),
        sa.Column("set_by", sa.Text(), nullable=True),
        sa.Column("set_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False),
        sa.Column("confirmed_by", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["people.id"], name=op.f("fk_classifications_confirmed_by_people")
        ),
        sa.ForeignKeyConstraint(
            ["dimension_id"], ["dimensions.id"], name=op.f("fk_classifications_dimension_id_dimensions")
        ),
        sa.ForeignKeyConstraint(["set_by"], ["people.id"], name=op.f("fk_classifications_set_by_people")),
        sa.ForeignKeyConstraint(
            ["value_id"], ["dimension_values.id"], name=op.f("fk_classifications_value_id_dimension_values")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_classifications_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classifications")),
        sa.UniqueConstraint(
            "target_type",
            "target_id",
            "dimension_id",
            "value_id",
            name=op.f("uq_classifications_target_type_target_id_dimension_id_value_id"),
        ),
    )
    op.create_index(
        op.f("ix_classifications_workspace_id"), "classifications", ["workspace_id"], unique=False
    )
    op.create_table(
        "components",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("lead_id", sa.Text(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["lead_id"], ["people.id"], name=op.f("fk_components_lead_id_people")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_components_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_components")),
    )
    op.create_table(
        "custom_field_definitions",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "field_type",
            sa.Enum(
                "text",
                "number",
                "date",
                "select",
                "multi_select",
                "person",
                "url",
                "bool",
                name="fieldtype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("applies_to_types", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_custom_field_definitions_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_custom_field_definitions_workspace_id_workspaces"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_field_definitions")),
    )
    op.create_index(
        op.f("ix_custom_field_definitions_workspace_id"),
        "custom_field_definitions",
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        "iterations",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column(
            "state",
            sa.Enum("planned", "active", "closed", name="iterationstate", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("committed_points", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("completed_points", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_iterations_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_iterations")),
    )
    op.create_table(
        "product_links",
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "delivers",
                "supports",
                "depends_on",
                "owns",
                name="productlinkrole",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("weight", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_product_links_product_id_products")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_links")),
    )
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "manager",
                "contributor",
                "stakeholder",
                "viewer",
                name="projectrole",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("allocation_pct", sa.Integer(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["person_id"], ["people.id"], name=op.f("fk_project_members_person_id_people")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_project_members_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_members")),
    )
    op.create_table(
        "releases",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column(
            "state",
            sa.Enum("unreleased", "released", "archived", name="releasestate", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_releases_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_releases")),
    )
    op.create_table(
        "risks",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "category",
            sa.Enum(
                "schedule",
                "scope",
                "budget",
                "technical",
                "resource",
                "external",
                "compliance",
                name="riskcategory",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("probability", sa.Integer(), nullable=False),
        sa.Column("impact", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "identified",
                "analysing",
                "mitigating",
                "monitoring",
                "occurred",
                "closed",
                name="riskstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("trigger", sa.Text(), nullable=True),
        sa.Column(
            "response_strategy",
            sa.Enum(
                "avoid", "mitigate", "transfer", "accept", name="riskresponse", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("review_date", sa.Date(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["people.id"], name=op.f("fk_risks_owner_id_people")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name=op.f("fk_risks_project_id_projects")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risks")),
        sa.UniqueConstraint("project_id", "key", name=op.f("uq_risks_project_id_key")),
    )
    op.create_table(
        "stage_transition_rules",
        sa.Column("process_id", sa.Text(), nullable=False),
        sa.Column("to_stage_id", sa.Text(), nullable=False),
        sa.Column("from_stage_id", sa.Text(), nullable=True),
        sa.Column("allowed_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_evidence_kinds", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("auto_when", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["from_stage_id"], ["stages.id"], name=op.f("fk_stage_transition_rules_from_stage_id_stages")
        ),
        sa.ForeignKeyConstraint(
            ["process_id"], ["processes.id"], name=op.f("fk_stage_transition_rules_process_id_processes")
        ),
        sa.ForeignKeyConstraint(
            ["to_stage_id"], ["stages.id"], name=op.f("fk_stage_transition_rules_to_stage_id_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stage_transition_rules")),
    )
    op.create_table(
        "violations",
        sa.Column("policy_id", sa.Text(), nullable=False),
        sa.Column(
            "target_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "info", "warning", "violation", "critical", name="severity", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.Enum(
                "process",
                "quality",
                "time",
                "planning",
                "collaboration",
                "compliance",
                "wellbeing",
                name="policycategory",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("person_id", sa.Text(), nullable=True),
        sa.Column("team_id", sa.Text(), nullable=True),
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("product_id", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "acknowledged",
                "resolved",
                "waived",
                "false_positive",
                name="violationstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Text(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("waived_by", sa.Text(), nullable=True),
        sa.Column("waive_reason", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name=op.f("fk_violations_person_id_people")),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["policies.id"], name=op.f("fk_violations_policy_id_policies")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], name=op.f("fk_violations_product_id_products")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_violations_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"], ["people.id"], name=op.f("fk_violations_resolved_by_people")
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_violations_team_id_teams")),
        sa.ForeignKeyConstraint(["waived_by"], ["people.id"], name=op.f("fk_violations_waived_by_people")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_violations_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_violations")),
    )
    op.create_index(op.f("ix_violations_workspace_id"), "violations", ["workspace_id"], unique=False)
    op.create_index(
        "ix_violations_workspace_status_severity",
        "violations",
        ["workspace_id", "status", "severity"],
        unique=False,
    )
    op.create_table(
        "workspace_members",
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("handle", sa.Text(), nullable=False),
        sa.Column("capacity_hours_per_week", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("cost_rate", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column(
            "status",
            sa.Enum("active", "invited", "suspended", name="memberstatus", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("inbox_project_id", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["inbox_project_id"], ["projects.id"], name=op.f("fk_workspace_members_inbox_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["person_id"], ["people.id"], name=op.f("fk_workspace_members_person_id_people")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_workspace_members_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_members")),
        sa.UniqueConstraint("workspace_id", "handle", name=op.f("uq_workspace_members_workspace_id_handle")),
        sa.UniqueConstraint(
            "workspace_id", "person_id", name=op.f("uq_workspace_members_workspace_id_person_id")
        ),
    )
    op.create_index(
        op.f("ix_workspace_members_workspace_id"), "workspace_members", ["workspace_id"], unique=False
    )
    op.create_table(
        "approval_decisions",
        sa.Column("approval_id", sa.Text(), nullable=False),
        sa.Column("approver_id", sa.Text(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "pending", "approved", "rejected", "abstained", name="decision", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.id"], name=op.f("fk_approval_decisions_approval_id_approvals")
        ),
        sa.ForeignKeyConstraint(
            ["approver_id"], ["people.id"], name=op.f("fk_approval_decisions_approver_id_people")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_decisions")),
    )
    op.create_table(
        "milestones",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "kind",
            sa.Enum(
                "gate",
                "release",
                "demo",
                "deadline",
                "other",
                name="milestonekind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("approval_id", sa.Text(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.id"], name=op.f("fk_milestones_approval_id_approvals")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_milestones_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_milestones")),
    )
    op.create_table(
        "stage_placements",
        sa.Column(
            "target_type",
            sa.Enum(
                "work_item",
                "project",
                "product",
                "activity",
                "person",
                "risk",
                "milestone",
                "release",
                "baseline",
                "context_note",
                "comment",
                "daily_log",
                "saved_view",
                "stage_placement",
                "team",
                "objective",
                name="targettype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("process_id", sa.Text(), nullable=False),
        sa.Column("stage_id", sa.Text(), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moved_by", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("approval_id", sa.Text(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        postgresql.ExcludeConstraint(
            (sa.column("target_type"), "="),
            (sa.column("target_id"), "="),
            (sa.column("process_id"), "="),
            (sa.text("tstzrange(entered_at, exited_at, '[)')"), "&&"),
            using="gist",
            name="ex_stage_placements_one_current",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approvals.id"], name=op.f("fk_stage_placements_approval_id_approvals")
        ),
        sa.ForeignKeyConstraint(
            ["moved_by"], ["people.id"], name=op.f("fk_stage_placements_moved_by_people")
        ),
        sa.ForeignKeyConstraint(
            ["process_id"], ["processes.id"], name=op.f("fk_stage_placements_process_id_processes")
        ),
        sa.ForeignKeyConstraint(
            ["stage_id"], ["stages.id"], name=op.f("fk_stage_placements_stage_id_stages")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stage_placements")),
    )
    op.create_table(
        "work_items",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("type_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("state_id", sa.Text(), nullable=False),
        sa.Column("reporter_id", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Text(), nullable=True),
        sa.Column("path", LTree(), server_default="", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "priority",
            sa.Enum(
                "lowest",
                "low",
                "medium",
                "high",
                "highest",
                "critical",
                name="priority",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "priority_source",
            sa.Enum(
                "manual",
                "inherited",
                "rule",
                "import",
                "formula",
                name="valuesource",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("severity", sa.Text(), nullable=True),
        sa.Column("assignee_id", sa.Text(), nullable=True),
        sa.Column("team_id", sa.Text(), nullable=True),
        sa.Column("iteration_id", sa.Text(), nullable=True),
        sa.Column("milestone_id", sa.Text(), nullable=True),
        sa.Column("release_id", sa.Text(), nullable=True),
        sa.Column("component_id", sa.Text(), nullable=True),
        sa.Column("planned_start", sa.Date(), nullable=True),
        sa.Column("planned_end", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimate_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column(
            "estimate_unit",
            sa.Enum("points", "hours", "days", "none", name="estimationunit", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("remaining_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("weight", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("weight_scheme_id", sa.Text(), nullable=True),
        sa.Column("impact", sa.Integer(), nullable=True),
        sa.Column("value_score", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column(
            "progress_mode",
            sa.Enum(
                "manual", "children", "checklist", "time", name="progressmode", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("rank", sa.Text(), nullable=False),
        sa.Column("is_provisional", sa.Boolean(), nullable=False),
        sa.Column("is_template", sa.Boolean(), nullable=False),
        sa.Column("template_item_id", sa.Text(), nullable=True),
        sa.Column(
            "visibility",
            sa.Enum(
                "inherit",
                "private",
                "assignees",
                "members",
                "team",
                "project",
                "workspace",
                "scope_members",
                "managers_only",
                name="visibility",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("source", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("external_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["people.id"], name=op.f("fk_work_items_assignee_id_people")
        ),
        sa.ForeignKeyConstraint(
            ["component_id"], ["components.id"], name=op.f("fk_work_items_component_id_components")
        ),
        sa.ForeignKeyConstraint(
            ["iteration_id"], ["iterations.id"], name=op.f("fk_work_items_iteration_id_iterations")
        ),
        sa.ForeignKeyConstraint(
            ["milestone_id"], ["milestones.id"], name=op.f("fk_work_items_milestone_id_milestones")
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["work_items.id"], name=op.f("fk_work_items_parent_id_work_items")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_work_items_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["release_id"], ["releases.id"], name=op.f("fk_work_items_release_id_releases")
        ),
        sa.ForeignKeyConstraint(
            ["reporter_id"], ["people.id"], name=op.f("fk_work_items_reporter_id_people")
        ),
        sa.ForeignKeyConstraint(
            ["state_id"], ["workflow_states.id"], name=op.f("fk_work_items_state_id_workflow_states")
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_work_items_team_id_teams")),
        sa.ForeignKeyConstraint(
            ["template_item_id"], ["work_items.id"], name=op.f("fk_work_items_template_item_id_work_items")
        ),
        sa.ForeignKeyConstraint(
            ["type_id"], ["work_item_types.id"], name=op.f("fk_work_items_type_id_work_item_types")
        ),
        sa.ForeignKeyConstraint(
            ["weight_scheme_id"],
            ["weight_schemes.id"],
            name=op.f("fk_work_items_weight_scheme_id_weight_schemes"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_items")),
        sa.UniqueConstraint("project_id", "key", name=op.f("uq_work_items_project_id_key")),
    )
    op.create_index("ix_work_items_path", "work_items", ["path"], unique=False, postgresql_using="gist")
    op.create_index(
        "ix_work_items_title_trgm",
        "work_items",
        ["title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_table(
        "activities",
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "working",
                "reviewing",
                "meeting",
                "blocked",
                "researching",
                "waiting",
                "other",
                name="activitykind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("active", "paused", "ended", name="activitystatus", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("client_meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mood", sa.Integer(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name=op.f("fk_activities_person_id_people")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_activities_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_activities_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activities")),
    )
    op.create_index(
        "ix_activities_person_active",
        "activities",
        ["person_id"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "assignments",
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "contributor",
                "reviewer",
                "approver",
                "watcher",
                name="assignmentrole",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name=op.f("fk_assignments_person_id_people")),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_assignments_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignments")),
    )
    op.create_table(
        "baselines",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("baseline_name", sa.Text(), nullable=False),
        sa.Column("captured_by", sa.Text(), nullable=False),
        sa.Column("planned_start", sa.Date(), nullable=True),
        sa.Column("planned_end", sa.Date(), nullable=True),
        sa.Column("estimate_value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["captured_by"], ["people.id"], name=op.f("fk_baselines_captured_by_people")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_baselines_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_baselines_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_baselines")),
    )
    op.create_table(
        "checklists",
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_checklists_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checklists")),
    )
    op.create_table(
        "custom_field_values",
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("field_definition_id", sa.Text(), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_number", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("value_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["field_definition_id"],
            ["custom_field_definitions.id"],
            name=op.f("fk_custom_field_values_field_definition_id_custom_field_definitions"),
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_custom_field_values_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_field_values")),
    )
    op.create_table(
        "estimates",
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column(
            "unit",
            sa.Enum("points", "hours", "days", "none", name="estimationunit", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "kind",
            sa.Enum("original", "revised", "remaining", name="estimatekind", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name=op.f("fk_estimates_person_id_people")),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_estimates_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_estimates")),
    )
    op.create_table(
        "recurrence_rules",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("template_item_id", sa.Text(), nullable=False),
        sa.Column("rrule", sa.Text(), nullable=False),
        sa.Column("time_zone", sa.Text(), nullable=False),
        sa.Column("assign_to", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("lead_days", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_recurrence_rules_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["template_item_id"],
            ["work_items.id"],
            name=op.f("fk_recurrence_rules_template_item_id_work_items"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recurrence_rules")),
    )
    op.create_table(
        "risk_actions",
        sa.Column("risk_id", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("work_item_id", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["risk_id"], ["risks.id"], name=op.f("fk_risk_actions_risk_id_risks")),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_risk_actions_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_actions")),
    )
    op.create_table(
        "risk_links",
        sa.Column("risk_id", sa.Text(), nullable=False),
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("inherited", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["risk_id"], ["risks.id"], name=op.f("fk_risk_links_risk_id_risks")),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_risk_links_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_links")),
    )
    op.create_table(
        "weights",
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("scheme_id", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "manual",
                "inherited",
                "rule",
                "import",
                "formula",
                name="valuesource",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("set_by", sa.Text(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["scheme_id"], ["weight_schemes.id"], name=op.f("fk_weights_scheme_id_weight_schemes")
        ),
        sa.ForeignKeyConstraint(["set_by"], ["people.id"], name=op.f("fk_weights_set_by_people")),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_weights_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weights")),
    )
    op.create_table(
        "work_item_links",
        sa.Column("from_item_id", sa.Text(), nullable=False),
        sa.Column("to_item_id", sa.Text(), nullable=False),
        sa.Column(
            "link_type",
            sa.Enum(
                "blocks",
                "is_blocked_by",
                "relates_to",
                "duplicates",
                "clones",
                "depends_on",
                "finish_to_start",
                "start_to_start",
                "finish_to_finish",
                name="linktype",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("lag_days", sa.Integer(), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["from_item_id"], ["work_items.id"], name=op.f("fk_work_item_links_from_item_id_work_items")
        ),
        sa.ForeignKeyConstraint(
            ["to_item_id"], ["work_items.id"], name=op.f("fk_work_item_links_to_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_item_links")),
    )
    op.create_table(
        "checklist_items",
        sa.Column("checklist_id", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_done", sa.Boolean(), nullable=False),
        sa.Column("done_by", sa.Text(), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["checklist_id"], ["checklists.id"], name=op.f("fk_checklist_items_checklist_id_checklists")
        ),
        sa.ForeignKeyConstraint(["done_by"], ["people.id"], name=op.f("fk_checklist_items_done_by_people")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checklist_items")),
    )
    op.create_table(
        "evidence",
        sa.Column("activity_id", sa.Text(), nullable=False),
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "note",
                "screenshot",
                "link",
                "commit",
                "pr",
                "document",
                "file",
                "metric",
                name="evidencekind",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("attachment_id", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "manual",
                "client_auto",
                "integration",
                "timer",
                "imported",
                name="capturesource",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["activity_id"], ["activities.id"], name=op.f("fk_evidence_activity_id_activities")
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"], ["attachments.id"], name=op.f("fk_evidence_attachment_id_attachments")
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name=op.f("fk_evidence_person_id_people")),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_evidence_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence")),
    )
    op.create_table(
        "time_entries",
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("activity_id", sa.Text(), nullable=True),
        sa.Column("is_billable", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "manual",
                "client_auto",
                "integration",
                "timer",
                "imported",
                name="capturesource",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["activity_id"], ["activities.id"], name=op.f("fk_time_entries_activity_id_activities")
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name=op.f("fk_time_entries_person_id_people")),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_time_entries_work_item_id_work_items")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_entries")),
    )


def downgrade() -> None:
    op.drop_table("time_entries")
    op.drop_table("evidence")
    op.drop_table("checklist_items")
    op.drop_table("work_item_links")
    op.drop_table("weights")
    op.drop_table("risk_links")
    op.drop_table("risk_actions")
    op.drop_table("recurrence_rules")
    op.drop_table("estimates")
    op.drop_table("custom_field_values")
    op.drop_table("checklists")
    op.drop_table("baselines")
    op.drop_table("assignments")
    op.drop_index(
        "ix_activities_person_active", table_name="activities", postgresql_where=sa.text("status = 'active'")
    )
    op.drop_table("activities")
    op.drop_index(
        "ix_work_items_title_trgm",
        table_name="work_items",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.drop_index("ix_work_items_path", table_name="work_items", postgresql_using="gist")
    op.drop_table("work_items")
    op.drop_table("stage_placements")
    op.drop_table("milestones")
    op.drop_table("approval_decisions")
    op.drop_index(op.f("ix_workspace_members_workspace_id"), table_name="workspace_members")
    op.drop_table("workspace_members")
    op.drop_index("ix_violations_workspace_status_severity", table_name="violations")
    op.drop_index(op.f("ix_violations_workspace_id"), table_name="violations")
    op.drop_table("violations")
    op.drop_table("stage_transition_rules")
    op.drop_table("risks")
    op.drop_table("releases")
    op.drop_table("project_members")
    op.drop_table("product_links")
    op.drop_table("iterations")
    op.drop_index(op.f("ix_custom_field_definitions_workspace_id"), table_name="custom_field_definitions")
    op.drop_table("custom_field_definitions")
    op.drop_table("components")
    op.drop_index(op.f("ix_classifications_workspace_id"), table_name="classifications")
    op.drop_table("classifications")
    op.drop_table("approvals")
    op.drop_table("workflow_states")
    op.drop_index(op.f("ix_work_item_types_workspace_id"), table_name="work_item_types")
    op.drop_table("work_item_types")
    op.drop_table("tag_links")
    op.drop_table("stages")
    op.drop_index(op.f("ix_saved_views_workspace_id"), table_name="saved_views")
    op.drop_table("saved_views")
    op.drop_table("role_assignments")
    op.drop_index(op.f("ix_projects_workspace_id"), table_name="projects")
    op.drop_table("projects")
    op.drop_index(op.f("ix_products_workspace_id"), table_name="products")
    op.drop_table("products")
    op.drop_table("policy_test_runs")
    op.drop_table("metric_snapshots")
    op.drop_table("memberships")
    op.drop_table("key_results")
    op.drop_table("dimension_values")
    op.drop_index(op.f("ix_workflows_workspace_id"), table_name="workflows")
    op.drop_table("workflows")
    op.drop_index(op.f("ix_weight_schemes_workspace_id"), table_name="weight_schemes")
    op.drop_table("weight_schemes")
    op.drop_index(op.f("ix_teams_workspace_id"), table_name="teams")
    op.drop_table("teams")
    op.drop_index(op.f("ix_tags_workspace_id"), table_name="tags")
    op.drop_table("tags")
    op.drop_table("roles")
    op.drop_index(op.f("ix_reporting_lines_workspace_id"), table_name="reporting_lines")
    op.drop_table("reporting_lines")
    op.drop_index(op.f("ix_processes_workspace_id"), table_name="processes")
    op.drop_table("processes")
    op.drop_table("policy_templates")
    op.drop_index(op.f("ix_policies_workspace_id"), table_name="policies")
    op.drop_table("policies")
    op.drop_index(op.f("ix_objectives_workspace_id"), table_name="objectives")
    op.drop_table("objectives")
    op.drop_index(op.f("ix_habits_workspace_id"), table_name="habits")
    op.drop_table("habits")
    op.drop_table("habit_definitions")
    op.drop_index(
        "uq_events_client_id", table_name="events", postgresql_where=sa.text("client_id IS NOT NULL")
    )
    op.drop_index("ix_events_workspace_occurred", table_name="events")
    op.drop_index(op.f("ix_events_workspace_id"), table_name="events")
    op.drop_index("ix_events_entity", table_name="events")
    op.drop_table("events")
    op.drop_index(op.f("ix_dimensions_workspace_id"), table_name="dimensions")
    op.drop_table("dimensions")
    op.drop_index(op.f("ix_daily_logs_workspace_id"), table_name="daily_logs")
    op.drop_table("daily_logs")
    op.drop_index(op.f("ix_context_notes_workspace_id"), table_name="context_notes")
    op.drop_table("context_notes")
    op.drop_table("comments")
    op.drop_table("attachments")
    op.drop_index(op.f("ix_access_grants_workspace_id"), table_name="access_grants")
    op.drop_table("access_grants")
    op.drop_table("workspaces")
    op.drop_table("people")
    op.drop_table("insights")
