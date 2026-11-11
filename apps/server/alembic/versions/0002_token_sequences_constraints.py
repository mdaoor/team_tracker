"""token_sequences_constraints

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table(
        "api_tokens",
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name=op.f("fk_api_tokens_person_id_people")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_api_tokens_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_tokens")),
        sa.UniqueConstraint("hash", name=op.f("uq_api_tokens_hash")),
    )
    op.create_index(op.f("ix_api_tokens_workspace_id"), "api_tokens", ["workspace_id"], unique=False)
    op.create_table(
        "review_items",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("suggestion", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_review_items_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_items")),
    )
    op.create_index(op.f("ix_review_items_workspace_id"), "review_items", ["workspace_id"], unique=False)
    op.create_index(
        "ix_review_items_workspace_status", "review_items", ["workspace_id", "status"], unique=False
    )
    op.create_table(
        "project_key_sequences",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("next", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_project_key_sequences_project_id_projects")
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_project_key_sequences")),
    )
    op.create_unique_constraint(
        op.f("uq_daily_logs_workspace_id_person_id_date"), "daily_logs", ["workspace_id", "person_id", "date"]
    )
    op.create_index(
        "uq_reporting_lines_primary_line",
        "reporting_lines",
        ["person_id"],
        unique=True,
        postgresql_where=sa.text("is_primary AND kind = 'line' AND deleted_at IS NULL"),
    )


def downgrade() -> None:

    op.drop_index(
        "uq_reporting_lines_primary_line",
        table_name="reporting_lines",
        postgresql_where=sa.text("is_primary AND kind = 'line' AND deleted_at IS NULL"),
    )
    op.drop_constraint(op.f("uq_daily_logs_workspace_id_person_id_date"), "daily_logs", type_="unique")
    op.drop_table("project_key_sequences")
    op.drop_index("ix_review_items_workspace_status", table_name="review_items")
    op.drop_index(op.f("ix_review_items_workspace_id"), table_name="review_items")
    op.drop_table("review_items")
    op.drop_index(op.f("ix_api_tokens_workspace_id"), table_name="api_tokens")
    op.drop_table("api_tokens")
