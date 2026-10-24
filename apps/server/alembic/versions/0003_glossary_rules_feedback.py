"""glossary_rules_feedback

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table(
        "translator_feedback",
        sa.Column("person_id", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=True),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("raw", sa.Text(), nullable=True),
        sa.Column("previous", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("corrected", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["person_id"], ["people.id"], name=op.f("fk_translator_feedback_person_id_people")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_translator_feedback_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_translator_feedback")),
    )
    op.create_index(
        op.f("ix_translator_feedback_workspace_id"), "translator_feedback", ["workspace_id"], unique=False
    )
    op.create_table(
        "translator_rules",
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_translator_rules_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_translator_rules")),
    )
    op.create_index(
        op.f("ix_translator_rules_workspace_id"), "translator_rules", ["workspace_id"], unique=False
    )
    op.create_table(
        "glossary_entries",
        sa.Column("project_id", sa.Text(), nullable=True),
        sa.Column("phrase", sa.Text(), nullable=False),
        sa.Column("resolves_to_type", sa.Text(), nullable=False),
        sa.Column("resolves_to_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_glossary_entries_project_id_projects")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_glossary_entries_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_glossary_entries")),
    )
    op.create_index(
        op.f("ix_glossary_entries_workspace_id"), "glossary_entries", ["workspace_id"], unique=False
    )
    op.create_index(
        "uq_glossary_phrase",
        "glossary_entries",
        ["workspace_id", sa.literal_column("lower(phrase)")],
        unique=True,
    )


def downgrade() -> None:

    op.drop_index("uq_glossary_phrase", table_name="glossary_entries")
    op.drop_index(op.f("ix_glossary_entries_workspace_id"), table_name="glossary_entries")
    op.drop_table("glossary_entries")
    op.drop_index(op.f("ix_translator_rules_workspace_id"), table_name="translator_rules")
    op.drop_table("translator_rules")
    op.drop_index(op.f("ix_translator_feedback_workspace_id"), table_name="translator_feedback")
    op.drop_table("translator_feedback")
