"""access_control

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table(
        "idp_group_mappings",
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("group", sa.Text(), nullable=False),
        sa.Column("role_key", sa.Text(), nullable=True),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=True),
        sa.Column("team_id", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name=op.f("fk_idp_group_mappings_team_id_teams")),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f("fk_idp_group_mappings_workspace_id_workspaces")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idp_group_mappings")),
    )
    op.create_index(
        "ix_idp_group_mappings_group",
        "idp_group_mappings",
        ["workspace_id", sa.literal_column('lower("group")')],
        unique=False,
    )
    op.create_index(
        op.f("ix_idp_group_mappings_workspace_id"), "idp_group_mappings", ["workspace_id"], unique=False
    )
    op.add_column(
        "api_tokens",
        sa.Column(
            "scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text('\'["capture", "read"]\'::jsonb'),
            nullable=False,
        ),
    )
    op.add_column("memberships", sa.Column("source", sa.Text(), server_default="manual", nullable=False))
    op.add_column("role_assignments", sa.Column("source", sa.Text(), server_default="manual", nullable=False))
    op.create_index(
        "uq_roles_builtin_key",
        "roles",
        ["key"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
    )
    op.create_index(
        "uq_roles_workspace_key",
        "roles",
        ["workspace_id", "key"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NOT NULL"),
    )


def downgrade() -> None:

    op.drop_index(
        "uq_roles_workspace_key", table_name="roles", postgresql_where=sa.text("workspace_id IS NOT NULL")
    )
    op.drop_index(
        "uq_roles_builtin_key", table_name="roles", postgresql_where=sa.text("workspace_id IS NULL")
    )
    op.drop_column("role_assignments", "source")
    op.drop_column("memberships", "source")
    op.drop_column("api_tokens", "scopes")
    op.drop_index(op.f("ix_idp_group_mappings_workspace_id"), table_name="idp_group_mappings")
    op.drop_index("ix_idp_group_mappings_group", table_name="idp_group_mappings")
    op.drop_table("idp_group_mappings")
