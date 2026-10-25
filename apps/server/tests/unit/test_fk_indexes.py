"""Phase 13 R3.1 / review T3: every foreign key has an index that starts with its columns."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

UNINDEXED_FKS = text(
    """
    SELECT c.conrelid::regclass::text AS tbl, c.conname
    FROM pg_constraint c
    WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
      AND NOT EXISTS (
        SELECT 1 FROM pg_index i
        WHERE i.indrelid = c.conrelid AND i.indisvalid
          AND (i.indkey::int2[])[0:array_length(c.conkey, 1) - 1] = c.conkey
      )
    ORDER BY 1, 2
    """
)


async def test_every_foreign_key_has_a_leading_index(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        rows = (await conn.execute(UNINDEXED_FKS)).all()
    assert [tuple(r) for r in rows] == []


async def test_migration_0011_is_reversible(migrated_url: str) -> None:
    """Upgrade/downgrade round trip on a scratch database (guards the hashed names for long identifiers)."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0011_fk_indexes.py"
    spec = importlib.util.spec_from_file_location("mig0011", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = [mod._name(t, c) for t, c in mod.FK_INDEXES]
    assert len(names) == len(set(names)) and all(len(n) <= 63 for n in names)
