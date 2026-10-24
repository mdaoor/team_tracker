"""Synthetic load for performance tests: N work items with the template item's classifications and stage placement.

Pure SQL (INSERT ... SELECT generate_series) so 50 k items take seconds. Used by tests/api/test_query_perf.py.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ITEM_COLS = (
    "project_id, type_id, state_id, reporter_id, priority, priority_source, severity, assignee_id, team_id, "
    "estimate_value, estimate_unit, remaining_value, weight, impact, progress_pct, progress_mode, rank, "
    "is_provisional, is_template, visibility, external_refs, due_date, planned_start, planned_end"
)


async def load_items(s: AsyncSession, template_key: str, n: int) -> int:
    """Clone `template_key` n times (keys LOAD-1..n) including classifications and the current stage placement."""
    await s.execute(
        text(
            f"""
            INSERT INTO work_items (id, key, path, title, {ITEM_COLS})
            SELECT 'load-' || g, 'LOAD-' || g, text2ltree('load' || g), 'load item ' || g || ' ' || (ARRAY['login','checkout','pipeline','retry','report'])[1 + g % 5],
                   {", ".join("w." + c.strip() for c in ITEM_COLS.split(","))}
            FROM work_items w, generate_series(1, :n) g WHERE w.key = :k
            """
        ),
        {"n": n, "k": template_key},
    )
    await s.execute(
        text(
            """
            INSERT INTO classifications (id, workspace_id, target_type, target_id, dimension_id, value_id, source, set_at, is_confirmed)
            SELECT gen_random_uuid()::text, c.workspace_id, c.target_type, 'load-' || g, c.dimension_id, c.value_id, c.source, now(), true
            FROM classifications c JOIN work_items w ON w.id = c.target_id AND c.target_type = 'work_item', generate_series(1, :n) g
            WHERE w.key = :k AND c.deleted_at IS NULL
            """
        ),
        {"n": n, "k": template_key},
    )
    await s.execute(
        text(
            """
            INSERT INTO stage_placements (id, target_type, target_id, process_id, stage_id, entered_at)
            SELECT gen_random_uuid()::text, p.target_type, 'load-' || g, p.process_id, p.stage_id, now() - (g % 20) * interval '1 day'
            FROM stage_placements p JOIN work_items w ON w.id = p.target_id AND p.target_type = 'work_item', generate_series(1, :n) g
            WHERE w.key = :k AND p.exited_at IS NULL
            """
        ),
        {"n": n, "k": template_key},
    )
    await s.commit()
    return n
