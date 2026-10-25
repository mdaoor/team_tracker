"""personal tracker: time entries remember their client message and calendar occurrence

Revision ID: 0013
Revises: 0010

Phase 15 (ADR-0042). Named 0013 because Phase 13 owns 0011/0012; at merge only `down_revision` changes.
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("time_entries", sa.Column("workspace_id", sa.Text(), nullable=True))
    op.add_column("time_entries", sa.Column("client_id", sa.Text(), nullable=True))
    op.add_column("time_entries", sa.Column("calendar_provider", sa.Text(), nullable=True))
    op.add_column("time_entries", sa.Column("calendar_event_id", sa.Text(), nullable=True))
    op.add_column("time_entries", sa.Column("calendar_instance_start", sa.Text(), nullable=True))
    op.create_foreign_key(
        op.f("fk_time_entries_workspace_id_workspaces"),
        "time_entries",
        "workspaces",
        ["workspace_id"],
        ["id"],
    )
    op.create_index(
        "uq_time_entries_calendar_instance",
        "time_entries",
        ["workspace_id", "person_id", "calendar_provider", "calendar_event_id", "calendar_instance_start"],
        unique=True,
        postgresql_where=sa.text("calendar_event_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_time_entries_client",
        "time_entries",
        ["workspace_id", "person_id", "client_id"],
        unique=False,
        postgresql_where=sa.text("client_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_time_entries_client", table_name="time_entries")
    op.drop_index("uq_time_entries_calendar_instance", table_name="time_entries")
    op.drop_constraint(op.f("fk_time_entries_workspace_id_workspaces"), "time_entries", type_="foreignkey")
    for c in (
        "calendar_instance_start",
        "calendar_event_id",
        "calendar_provider",
        "client_id",
        "workspace_id",
    ):
        op.drop_column("time_entries", c)
