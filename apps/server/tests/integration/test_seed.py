from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tt_server.db import models  # noqa: E402

from tools.seed.demo import build_demo, det_id, persist_demo  # noqa: E402


def test_build_is_deterministic() -> None:
    a = [r.id for r in build_demo()]
    b = [r.id for r in build_demo()]
    assert a == b and len(a) == len(set(a))


async def test_seed_matches_snapshot_counts(session_factory: async_sessionmaker[AsyncSession]) -> None:
    await persist_demo(session_factory)
    async with session_factory() as s:
        epic_type = det_id("item_type", "epic")

        async def count(stmt: object) -> int:
            return int(await s.scalar(stmt) or 0)  # type: ignore[arg-type]

        assert (
            await count(
                select(func.count()).select_from(models.WorkItem).where(models.WorkItem.type_id != epic_type)
            )
            == 42
        )
        assert (
            await count(
                select(func.count()).select_from(models.WorkItem).where(models.WorkItem.type_id == epic_type)
            )
            == 7
        )
        assert await count(select(func.count()).select_from(models.Project)) == 4
        assert await count(select(func.count()).select_from(models.Person)) == 8
        assert await count(select(func.count()).select_from(models.Milestone)) == 4
        assert await count(select(func.count()).select_from(models.Risk)) == 4
        keys = set((await s.scalars(select(models.WorkItem.key))).all())
        assert {"WEB-101", "GOV-125"} <= keys
        web101 = await s.get(models.WorkItem, det_id("work_item", "WEB-101"))
        assert web101 is not None and web101.key == "WEB-101" and web101.path.count(".") == 1


async def test_one_current_stage_placement_per_target(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from sqlalchemy.exc import IntegrityError
    from tt_schema.enums import TargetType
    from tt_schema.process import StagePlacement

    await persist_demo(session_factory)
    async with session_factory() as s:
        existing = (await s.scalars(select(models.StagePlacement).limit(1))).one()
        other_stage = (
            await s.scalars(select(models.Stage).where(models.Stage.id != existing.stage_id).limit(1))
        ).one()
        s.add(
            models.StagePlacement(
                **StagePlacement(
                    target_type=TargetType.WORK_ITEM,
                    target_id=existing.target_id,
                    process_id=existing.process_id,
                    stage_id=other_stage.id,
                ).model_dump()
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()
