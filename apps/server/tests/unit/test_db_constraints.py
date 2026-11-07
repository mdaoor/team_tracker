"""Phase 13 R4.4 (review T15): enums are checked and every FK states its delete rule, in the database."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tt_server.db.base import Base


async def test_every_non_native_enum_column_has_a_check_constraint(engine: AsyncEngine) -> None:
    import tt_server.db.models  # noqa: F401

    want = {
        (t.name, c.name)
        for t in Base.metadata.tables.values()
        if not t.name.startswith("procrastinate")
        for c in t.columns
        if isinstance(c.type, sa.Enum) and not c.type.native_enum
    }
    async with engine.connect() as conn:
        have = {
            r[0]
            for r in (
                await conn.execute(
                    text(
                        "select conname from pg_constraint where contype = 'c' and connamespace = 'public'::regnamespace"
                    )
                )
            ).all()
        }
    missing = sorted(f"{t}.{c}" for t, c in want if f"ck_{t}_{c}"[:63] not in have)
    assert missing == []


async def test_every_foreign_key_states_its_delete_rule(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        unspecified = (
            await conn.execute(
                text(
                    "select conrelid::regclass::text, conname from pg_constraint "
                    "where contype = 'f' and connamespace = 'public'::regnamespace and confdeltype = 'a' "
                    # procrastinate's own tables (procrastinate_events, _periodic_defers, ...) are created by the library's
                    # schema, not by our models, and its foreign keys are plain NO ACTION by design (job rows are deleted
                    # only through its own retention API). Every FK in *our* schema must say what happens on delete.
                    "and conrelid::regclass::text not like 'procrastinate%' order by 1, 2"
                )
            )
        ).all()
    assert [tuple(r) for r in unspecified] == []


async def test_owned_detail_rows_cascade_and_everything_else_restricts(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rule = dict(
            (
                await conn.execute(
                    text(
                        "select conrelid::regclass::text || '.' || (select attname from pg_attribute where attrelid = conrelid and attnum = conkey[1]), confdeltype "
                        "from pg_constraint where contype = 'f' and connamespace = 'public'::regnamespace"
                    )
                )
            ).all()
        )
    assert rule["checklist_items.checklist_id"] == "c" and rule["risk_links.risk_id"] == "c"
    assert rule["work_items.project_id"] == "r" and rule["time_entries.work_item_id"] == "r"
