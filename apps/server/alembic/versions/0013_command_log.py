"""Management-command log (Phase 14): idempotency by client_id and an audit row per command.

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "command_log",
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_command_log_workspace_id_workspaces"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["people.id"], name=op.f("fk_command_log_actor_id_people"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_command_log")),
        sa.UniqueConstraint("workspace_id", "client_id", name="uq_command_log_client"),
    )
    op.create_index(op.f("ix_command_log_workspace_id"), "command_log", ["workspace_id"])
    op.create_index(op.f("ix_command_log_actor_id"), "command_log", ["actor_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_command_log_actor_id"), table_name="command_log")
    op.drop_index(op.f("ix_command_log_workspace_id"), table_name="command_log")
    op.drop_table("command_log")
