"""review fixes: role_assignments.workspace_id, idp source stamping, hot-path indexes

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_activities_work_item_id", "activities", ["work_item_id"]),
    ("ix_activities_person_id", "activities", ["person_id"]),
    ("ix_time_entries_person_date", "time_entries", ["person_id", "date"]),
    ("ix_time_entries_work_item_id", "time_entries", ["work_item_id"]),
    ("ix_evidence_work_item_id", "evidence", ["work_item_id"]),
    ("ix_tag_links_target", "tag_links", ["target_type", "target_id"]),
    ("ix_work_items_assignee_id", "work_items", ["assignee_id"]),
    ("ix_work_items_reporter_id", "work_items", ["reporter_id"]),
    ("ix_work_items_state_id", "work_items", ["state_id"]),
    ("ix_milestones_project_id", "milestones", ["project_id"]),
    ("ix_iterations_project_id", "iterations", ["project_id"]),
]


def upgrade() -> None:
    op.add_column("role_assignments", sa.Column("workspace_id", sa.Text(), nullable=True))
    # backfill from the scope; person-scoped or unscoped rows fall back to the person's first workspace
    op.execute("UPDATE role_assignments SET workspace_id = scope_id WHERE scope_type = 'workspace'")
    for st, tbl in (("project", "projects"), ("product", "products"), ("team", "teams")):
        op.execute(
            f"UPDATE role_assignments ra SET workspace_id = t.workspace_id FROM {tbl} t "  # noqa: S608
            f"WHERE ra.workspace_id IS NULL AND ra.scope_type = '{st}' AND ra.scope_id = t.id"  # noqa: S608
        )
    op.execute(
        "UPDATE role_assignments ra SET workspace_id = (SELECT wm.workspace_id FROM workspace_members wm "
        "WHERE wm.person_id = ra.person_id ORDER BY wm.created_at LIMIT 1) WHERE ra.workspace_id IS NULL"
    )
    op.execute("DELETE FROM role_assignments WHERE workspace_id IS NULL")  # orphans nobody can see
    op.alter_column("role_assignments", "workspace_id", nullable=False)
    op.create_index("ix_role_assignments_workspace_id", "role_assignments", ["workspace_id"])
    op.create_foreign_key(
        "fk_role_assignments_workspace_id_workspaces",
        "role_assignments",
        "workspaces",
        ["workspace_id"],
        ["id"],
    )
    for table in ("role_assignments", "memberships"):  # which provider owns an idp-sourced row
        op.execute(f"UPDATE {table} SET source = 'idp:ldap' WHERE source = 'idp'")  # noqa: S608
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    for table in ("role_assignments", "memberships"):
        op.execute(f"UPDATE {table} SET source = 'idp' WHERE source LIKE 'idp:%'")  # noqa: S608
    op.drop_constraint("fk_role_assignments_workspace_id_workspaces", "role_assignments", type_="foreignkey")
    op.drop_index("ix_role_assignments_workspace_id", table_name="role_assignments")
    op.drop_column("role_assignments", "workspace_id")
