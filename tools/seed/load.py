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


async def load_members(s: AsyncSession, workspace_slug: str, n: int) -> int:
    """n synthetic people with workspace memberships (`@load1`..)."""
    await s.execute(
        text(
            """
            INSERT INTO people (id, display_name, email, kind, time_zone, is_active)
            SELECT 'loadp-' || g, 'Load Person ' || g, 'load' || g || '@load.test', 'human', 'UTC', true FROM generate_series(1, :n) g
            ON CONFLICT DO NOTHING
            """
        ),
        {"n": n},
    )
    await s.execute(
        text(
            """
            INSERT INTO workspace_members (id, workspace_id, person_id, handle, status)
            SELECT 'loadm-' || g, w.id, 'loadp-' || g, '@load' || g, 'active' FROM generate_series(1, :n) g, workspaces w WHERE w.slug = :slug
            ON CONFLICT DO NOTHING
            """
        ),
        {"n": n, "slug": workspace_slug},
    )
    await s.commit()
    return n


async def load_projects(s: AsyncSession, workspace_slug: str, template_project_key: str, n: int) -> int:
    """n clones of a project (keys `LP1`..) sharing the template's workflow and process."""
    await s.execute(
        text(
            """
            INSERT INTO projects (id, workspace_id, key, name, owner_id, workflow_id, default_item_type_id, kind, methodology, status,
                                  default_process_id, visibility, is_provisional, settings, external_refs)
            SELECT 'loadproj-' || g, p.workspace_id, 'LP' || g, 'Load project ' || g, p.owner_id, p.workflow_id, p.default_item_type_id, p.kind,
                   p.methodology, p.status, p.default_process_id, p.visibility, false, '{}'::jsonb, '{}'::jsonb
            FROM projects p JOIN workspaces w ON w.id = p.workspace_id, generate_series(1, :n) g
            WHERE w.slug = :slug AND p.key = :k
            ON CONFLICT DO NOTHING
            """
        ),
        {"n": n, "slug": workspace_slug, "k": template_project_key},
    )
    await s.commit()
    return n


async def load_events(s: AsyncSession, workspace_slug: str, n: int, *, batch: int = 200_000) -> int:
    """n `time.logged`-shaped events spread over the last 90 days, in batches so one statement never holds a million rows."""
    done = 0
    while done < n:
        k = min(batch, n - done)
        await s.execute(
            text(
                """
                INSERT INTO events (id, workspace_id, actor_id, entity_type, entity_id, event_type, payload, source, occurred_at, received_at)
                SELECT 'loadev-' || (:off + g), w.id, a.person_id, 'work_item', 'load-' || (1 + ((:off + g) % 1000)), 'item.updated',
                       jsonb_build_object('after', jsonb_build_object('n', :off + g)), 'overlay',
                       now() - ((:off + g) % 90) * interval '1 day' - ((:off + g) % 86400) * interval '1 second', now()
                FROM generate_series(1, :k) g, workspaces w
                JOIN LATERAL (SELECT person_id FROM workspace_members m WHERE m.workspace_id = w.id ORDER BY id LIMIT 1) a ON true
                WHERE w.slug = :slug
                """
            ),
            {"off": done, "k": k, "slug": workspace_slug},
        )
        await s.commit()
        done += k
    return done


async def _main() -> None:  # pragma: no cover - operator tool
    import argparse
    import time

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    ap = argparse.ArgumentParser(
        description="Load fixture: python -m tools.seed.load --url postgresql+psycopg://... [--items 100000 ...]"
    )
    ap.add_argument("--url", required=True)
    ap.add_argument("--workspace", default="acme")
    ap.add_argument("--members", type=int, default=200)
    ap.add_argument("--projects", type=int, default=50)
    ap.add_argument("--items", type=int, default=100_000)
    ap.add_argument("--events", type=int, default=1_000_000)
    a = ap.parse_args()
    eng = create_async_engine(a.url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        row = (
            await s.execute(
                text(
                    "SELECT i.key, p.key FROM work_items i JOIN projects p ON p.id = i.project_id ORDER BY i.created_at LIMIT 1"
                )
            )
        ).first()
        assert row, "seed the demo workspace first (make seed)"  # noqa: S101
        for label, coro in (
            ("members", load_members(s, a.workspace, a.members)),
            ("projects", load_projects(s, a.workspace, row[1], a.projects)),
            ("items (+classifications, placements)", load_items(s, row[0], a.items)),
            ("events", load_events(s, a.workspace, a.events)),
        ):
            t0 = time.perf_counter()
            n = await coro
            print(f"{label}: {n} in {time.perf_counter() - t0:.1f}s")  # noqa: T201
        await s.execute(text("ANALYZE"))
        await s.commit()
        _ = select
    await eng.dispose()


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
