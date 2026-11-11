"""Index every foreign-key column that has no leading index (Phase 13 R3.1, review T3).

The ACL context reads `project_members` and `memberships` on every authenticated request and several overlay paths join
through `assignments`, `estimates`, `stage_placements`...; without these the planner scans. A test asserts the invariant
for the whole schema (`tests/unit/test_fk_indexes.py`), so a future FK without an index fails CI.

Revision ID: 0011
Revises: 0010
"""

import hashlib

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

FK_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("access_grants", ("granted_by",)),
    ("attachments", ("uploader_id",)),
    ("comments", ("author_id",)),
    ("comments", ("parent_comment_id",)),
    ("context_notes", ("author_id",)),
    ("daily_logs", ("person_id",)),
    ("daily_logs", ("reviewed_by",)),
    ("events", ("actor_id",)),
    ("habit_definitions", ("workspace_id",)),
    ("objectives", ("owner_id",)),
    ("policies", ("owner_id",)),
    ("policy_templates", ("workspace_id",)),
    ("processes", ("owner_id",)),
    ("reporting_lines", ("manager_id",)),
    ("teams", ("lead_id",)),
    ("teams", ("parent_team_id",)),
    ("dimension_values", ("parent_value_id",)),
    ("key_results", ("objective_id",)),
    ("memberships", ("person_id",)),
    ("memberships", ("team_id",)),
    ("metric_snapshots", ("weight_scheme_id",)),
    ("policy_test_runs", ("policy_id",)),
    ("products", ("default_process_id",)),
    ("products", ("owner_id",)),
    ("products", ("parent_product_id",)),
    ("projects", ("default_process_id",)),
    ("projects", ("owner_id",)),
    ("projects", ("parent_project_id",)),
    ("projects", ("team_id",)),
    ("projects", ("workflow_id",)),
    ("role_assignments", ("person_id",)),
    ("role_assignments", ("role_id",)),
    ("saved_views", ("owner_id",)),
    ("saved_views", ("weight_scheme_id",)),
    ("tag_links", ("tag_id",)),
    ("work_item_types", ("default_workflow_id",)),
    ("workflow_states", ("workflow_id",)),
    ("approvals", ("project_id",)),
    ("approvals", ("requested_by",)),
    ("classifications", ("confirmed_by",)),
    ("classifications", ("set_by",)),
    ("classifications", ("value_id",)),
    ("components", ("lead_id",)),
    ("components", ("project_id",)),
    ("custom_field_definitions", ("project_id",)),
    ("product_links", ("product_id",)),
    ("project_members", ("person_id",)),
    ("project_members", ("project_id",)),
    ("releases", ("project_id",)),
    ("risks", ("owner_id",)),
    ("stage_transition_rules", ("from_stage_id",)),
    ("stage_transition_rules", ("process_id",)),
    ("stage_transition_rules", ("to_stage_id",)),
    ("violations", ("person_id",)),
    ("violations", ("product_id",)),
    ("violations", ("project_id",)),
    ("violations", ("resolved_by",)),
    ("violations", ("team_id",)),
    ("violations", ("waived_by",)),
    ("workspace_members", ("inbox_project_id",)),
    ("workspace_members", ("person_id",)),
    ("approval_decisions", ("approval_id",)),
    ("approval_decisions", ("approver_id",)),
    ("milestones", ("approval_id",)),
    ("stage_placements", ("approval_id",)),
    ("stage_placements", ("moved_by",)),
    ("stage_placements", ("process_id",)),
    ("stage_placements", ("stage_id",)),
    ("work_items", ("component_id",)),
    ("work_items", ("iteration_id",)),
    ("work_items", ("milestone_id",)),
    ("work_items", ("parent_id",)),
    ("work_items", ("release_id",)),
    ("work_items", ("team_id",)),
    ("work_items", ("template_item_id",)),
    ("work_items", ("type_id",)),
    ("work_items", ("weight_scheme_id",)),
    ("activities", ("project_id",)),
    ("assignments", ("person_id",)),
    ("assignments", ("work_item_id",)),
    ("baselines", ("captured_by",)),
    ("baselines", ("project_id",)),
    ("baselines", ("work_item_id",)),
    ("checklists", ("work_item_id",)),
    ("custom_field_values", ("field_definition_id",)),
    ("custom_field_values", ("work_item_id",)),
    ("estimates", ("person_id",)),
    ("estimates", ("work_item_id",)),
    ("recurrence_rules", ("project_id",)),
    ("recurrence_rules", ("template_item_id",)),
    ("risk_actions", ("risk_id",)),
    ("risk_actions", ("work_item_id",)),
    ("risk_links", ("risk_id",)),
    ("risk_links", ("work_item_id",)),
    ("weights", ("scheme_id",)),
    ("weights", ("set_by",)),
    ("weights", ("work_item_id",)),
    ("work_item_links", ("from_item_id",)),
    ("work_item_links", ("to_item_id",)),
    ("checklist_items", ("checklist_id",)),
    ("checklist_items", ("done_by",)),
    ("evidence", ("activity_id",)),
    ("evidence", ("attachment_id",)),
    ("evidence", ("person_id",)),
    ("time_entries", ("activity_id",)),
    ("api_tokens", ("person_id",)),
    ("translator_feedback", ("person_id",)),
    ("glossary_entries", ("project_id",)),
    ("idp_group_mappings", ("team_id",)),
    ("snapshot_cache", ("person_id",)),
    ("presentations", ("owner_id",)),
    ("presentations", ("weight_scheme_id",)),
    ("notifications", ("person_id",)),
    ("jira_worklogs", ("item_id",)),
    ("presentation_templates", ("owner_id",)),
)


def _name(table: str, cols: tuple[str, ...]) -> str:
    name = f"ix_{table}_{'_'.join(cols)}"
    if len(name) <= 63:
        return name
    return f"ix_{table[:30]}_{hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:10]}"


def upgrade() -> None:
    for table, cols in FK_INDEXES:
        op.create_index(_name(table, cols), table, list(cols), if_not_exists=True)


def downgrade() -> None:
    for table, cols in reversed(FK_INDEXES):
        op.drop_index(_name(table, cols), table_name=table, if_exists=True)
