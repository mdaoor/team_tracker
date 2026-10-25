"""Enum CHECK constraints and explicit ON DELETE rules (Phase 13 R4.4, review T15; ADR-0049).

* every `Enum(native_enum=False)` column gets `CHECK (col IN (...))`, so a bad value cannot be written around the application;
* every foreign key states what a parent delete does: CASCADE for owned detail rows (listed below), RESTRICT for the rest
  (hard deletes of referenced rows were never intended; soft delete is the rule).

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

ENUM_CHECKS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ('insights', 'scope_type', ('none', 'workspace', 'project', 'team', 'person', 'product', 'process', 'iteration')),
    ('insights', 'kind', ('summary', 'anomaly', 'recommendation')),
    ('insights', 'status', ('new', 'reviewed', 'actioned', 'dismissed')),
    ('people', 'kind', ('human', 'bot', 'external')),
    ('access_grants', 'subject_type', ('person', 'team', 'role')),
    ('access_grants', 'object_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('access_grants', 'level', ('read', 'comment', 'write', 'admin')),
    ('attachments', 'target_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('attachments', 'kind', ('file', 'link', 'image')),
    ('comments', 'target_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('context_notes', 'scope_type', ('none', 'workspace', 'project', 'team', 'person', 'product', 'process', 'iteration')),
    ('context_notes', 'kind', ('goal', 'okr', 'guideline', 'background', 'decision', 'link', 'onboarding', 'other')),
    ('context_notes', 'visibility', ('inherit', 'private', 'assignees', 'members', 'team', 'project', 'workspace', 'scope_members', 'managers_only')),
    ('daily_logs', 'status', ('draft', 'confirmed', 'reviewed')),
    ('dimensions', 'kind', ('builtin', 'custom')),
    ('dimensions', 'value_type', ('tree', 'flat')),
    ('dimensions', 'cardinality', ('single', 'multi')),
    ('events', 'entity_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('events', 'source', ('client', 'overlay', 'propagation', 'rule', 'import')),
    ('habit_definitions', 'polarity', ('positive', 'negative')),
    ('habit_definitions', 'category', ('process', 'quality', 'time', 'planning', 'collaboration', 'compliance', 'wellbeing')),
    ('habits', 'subject_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('habits', 'polarity', ('positive', 'negative')),
    ('habits', 'trend', ('improving', 'stable', 'worsening')),
    ('habits', 'status', ('observed', 'flagged', 'in_coaching', 'resolved')),
    ('habits', 'category', ('process', 'quality', 'time', 'planning', 'collaboration', 'compliance', 'wellbeing')),
    ('objectives', 'scope_type', ('none', 'workspace', 'project', 'team', 'person', 'product', 'process', 'iteration')),
    ('policies', 'target', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('policies', 'scope_type', ('none', 'workspace', 'project', 'team', 'person', 'product', 'process', 'iteration')),
    ('policies', 'trigger', ('on_event', 'scheduled')),
    ('policies', 'severity', ('info', 'warning', 'violation', 'critical')),
    ('policies', 'category', ('process', 'quality', 'time', 'planning', 'collaboration', 'compliance', 'wellbeing')),
    ('policy_templates', 'category', ('process', 'quality', 'time', 'planning', 'collaboration', 'compliance', 'wellbeing')),
    ('reporting_lines', 'kind', ('line', 'senior', 'project', 'functional', 'mentor', 'dotted')),
    ('reporting_lines', 'scope_type', ('none', 'workspace', 'project', 'team', 'person', 'product', 'process', 'iteration')),
    ('tags', 'kind', ('label', 'skill', 'area', 'client', 'custom')),
    ('teams', 'kind', ('org', 'virtual', 'ad_hoc')),
    ('weight_schemes', 'scope_type', ('none', 'workspace', 'project', 'team', 'person', 'product', 'process', 'iteration')),
    ('dimension_values', 'backing_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('memberships', 'role', ('lead', 'member', 'viewer')),
    ('metric_snapshots', 'scope_type', ('none', 'workspace', 'project', 'team', 'person', 'product', 'process', 'iteration')),
    ('products', 'status', ('concept', 'active', 'sunset', 'retired')),
    ('products', 'visibility', ('inherit', 'private', 'assignees', 'members', 'team', 'project', 'workspace', 'scope_members', 'managers_only')),
    ('projects', 'kind', ('standard', 'inbox', 'template')),
    ('projects', 'methodology', ('agile', 'waterfall', 'kanban', 'hybrid', 'none')),
    ('projects', 'status', ('draft', 'active', 'on_hold', 'completed', 'cancelled', 'archived')),
    ('projects', 'visibility', ('inherit', 'private', 'assignees', 'members', 'team', 'project', 'workspace', 'scope_members', 'managers_only')),
    ('role_assignments', 'scope_type', ('none', 'workspace', 'project', 'team', 'person', 'product', 'process', 'iteration')),
    ('saved_views', 'entity', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('saved_views', 'scope_type', ('none', 'workspace', 'project', 'team', 'person', 'product', 'process', 'iteration')),
    ('saved_views', 'layout', ('list', 'board', 'gantt', 'calendar', 'timeline', 'table', 'pipeline', 'matrix')),
    ('stages', 'category', ('ideation', 'planning', 'execution', 'validation', 'release', 'operate', 'closed')),
    ('stages', 'maps_to_state_category', ('open', 'active', 'done')),
    ('tag_links', 'target_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('work_item_types', 'key', ('epic', 'story', 'task', 'bug', 'subtask', 'spike', 'phase', 'deliverable', 'decision')),
    ('work_item_types', 'estimation_unit', ('points', 'hours', 'days', 'none')),
    ('workflow_states', 'category', ('open', 'active', 'done')),
    ('approvals', 'target_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('approvals', 'policy', ('any_one', 'all', 'quorum')),
    ('approvals', 'status', ('pending', 'approved', 'rejected', 'cancelled')),
    ('classifications', 'target_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('classifications', 'source', ('manual', 'inherited', 'rule', 'import', 'formula')),
    ('classifications', 'inherited_from_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('custom_field_definitions', 'field_type', ('text', 'number', 'date', 'select', 'multi_select', 'person', 'url', 'bool')),
    ('iterations', 'state', ('planned', 'active', 'closed')),
    ('product_links', 'target_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('product_links', 'role', ('delivers', 'supports', 'depends_on', 'owns')),
    ('project_members', 'role', ('owner', 'manager', 'contributor', 'stakeholder', 'viewer')),
    ('releases', 'state', ('unreleased', 'released', 'archived')),
    ('risks', 'target_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('risks', 'category', ('schedule', 'scope', 'budget', 'technical', 'resource', 'external', 'compliance')),
    ('risks', 'status', ('identified', 'analysing', 'mitigating', 'monitoring', 'occurred', 'closed')),
    ('risks', 'response_strategy', ('avoid', 'mitigate', 'transfer', 'accept')),
    ('violations', 'target_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('violations', 'severity', ('info', 'warning', 'violation', 'critical')),
    ('violations', 'category', ('process', 'quality', 'time', 'planning', 'collaboration', 'compliance', 'wellbeing')),
    ('violations', 'status', ('open', 'acknowledged', 'resolved', 'waived', 'false_positive')),
    ('workspace_members', 'status', ('active', 'invited', 'suspended')),
    ('approval_decisions', 'decision', ('pending', 'approved', 'rejected', 'abstained')),
    ('milestones', 'kind', ('gate', 'release', 'demo', 'deadline', 'other')),
    ('stage_placements', 'target_type', ('work_item', 'project', 'product', 'activity', 'person', 'risk', 'milestone', 'release', 'baseline', 'context_note', 'comment', 'daily_log', 'saved_view', 'stage_placement', 'team', 'objective')),
    ('work_items', 'priority', ('lowest', 'low', 'medium', 'high', 'highest', 'critical')),
    ('work_items', 'priority_source', ('manual', 'inherited', 'rule', 'import', 'formula')),
    ('work_items', 'estimate_unit', ('points', 'hours', 'days', 'none')),
    ('work_items', 'progress_mode', ('manual', 'children', 'checklist', 'time')),
    ('work_items', 'visibility', ('inherit', 'private', 'assignees', 'members', 'team', 'project', 'workspace', 'scope_members', 'managers_only')),
    ('activities', 'kind', ('working', 'reviewing', 'meeting', 'blocked', 'researching', 'waiting', 'other')),
    ('activities', 'status', ('active', 'paused', 'ended')),
    ('assignments', 'role', ('owner', 'contributor', 'reviewer', 'approver', 'watcher')),
    ('estimates', 'unit', ('points', 'hours', 'days', 'none')),
    ('estimates', 'kind', ('original', 'revised', 'remaining')),
    ('weights', 'source', ('manual', 'inherited', 'rule', 'import', 'formula')),
    ('work_item_links', 'link_type', ('blocks', 'is_blocked_by', 'relates_to', 'duplicates', 'clones', 'depends_on', 'finish_to_start', 'start_to_start', 'finish_to_finish')),
    ('evidence', 'kind', ('note', 'screenshot', 'link', 'commit', 'pr', 'document', 'file', 'metric')),
    ('evidence', 'source', ('manual', 'client_auto', 'integration', 'timer', 'imported')),
    ('time_entries', 'source', ('manual', 'client_auto', 'integration', 'timer', 'imported')),
)

# (table, column): rows that exist only as part of their parent and go with it
CASCADE: frozenset[tuple[str, str]] = frozenset(
    {
        ("checklist_items", "checklist_id"),
        ("checklists", "work_item_id"),
        ("approval_decisions", "approval_id"),
        ("risk_links", "risk_id"),
        ("risk_actions", "risk_id"),
        ("custom_field_values", "work_item_id"),
        ("custom_field_values", "field_definition_id"),
        ("weights", "work_item_id"),
        ("assignments", "work_item_id"),
        ("estimates", "work_item_id"),
        ("baselines", "work_item_id"),
        ("work_item_links", "from_item_id"),
        ("work_item_links", "to_item_id"),
        ("stage_transition_rules", "process_id"),
        ("project_key_sequences", "project_id"),
    }
)


def _name(table: str, col: str) -> str:
    return f"ck_{table}_{col}"[:63]


def upgrade() -> None:
    for table, col, values in ENUM_CHECKS:
        quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
        op.create_check_constraint(op.f(_name(table, col)), table, f'"{col}" IS NULL OR "{col}" IN ({quoted})')
    conn = op.get_bind()
    rows = conn.exec_driver_sql(
        """
        SELECT c.conname, c.conrelid::regclass::text, pg_get_constraintdef(c.oid),
               (SELECT a.attname FROM pg_attribute a WHERE a.attrelid = c.conrelid AND a.attnum = c.conkey[1])
        FROM pg_constraint c
        WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace AND c.confdeltype = 'a'
        """
    ).fetchall()
    for name, table, definition, col in rows:
        rule = "CASCADE" if (table.strip('"'), col) in CASCADE else "RESTRICT"
        head, sep, tail = definition.partition(" DEFERRABLE")
        new = f"{head} ON DELETE {rule}{sep}{tail}"
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"')
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT "{name}" {new}')


def downgrade() -> None:
    conn = op.get_bind()
    for name, table, definition in conn.exec_driver_sql(
        """
        SELECT c.conname, c.conrelid::regclass::text, pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace AND c.confdeltype IN ('r', 'c')
        """
    ).fetchall():
        plain = definition.replace(" ON DELETE RESTRICT", "").replace(" ON DELETE CASCADE", "")
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT "{name}"')
        op.execute(f'ALTER TABLE {table} ADD CONSTRAINT "{name}" {plain}')
    for table, col, _ in reversed(ENUM_CHECKS):
        op.drop_constraint(op.f(_name(table, col)), table, type_="check")
