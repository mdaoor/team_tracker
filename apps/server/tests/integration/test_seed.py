"""The demo seed (built through the services) still yields the snapshot counts and the weights of the oracle."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tt_schema.enums import TargetType  # noqa: E402
from tt_schema.process import StagePlacement  # noqa: E402
from tt_server.db import models  # noqa: E402

from tools.seed.demo import load_snapshot  # noqa: E402


async def test_seed_matches_snapshot_counts(seed_sf: async_sessionmaker[AsyncSession]) -> None:
    async with seed_sf() as s:
        epic_type = await s.scalar(select(models.WorkItemType.id).where(models.WorkItemType.key == "epic"))

        async def count(model: type, *where: object) -> int:
            return int(await s.scalar(select(func.count()).select_from(model).where(*where)) or 0)  # type: ignore[arg-type]

        assert await count(models.WorkItem, models.WorkItem.type_id != epic_type) == 42
        assert await count(models.WorkItem, models.WorkItem.type_id == epic_type) == 7
        assert await count(models.Project, models.Project.kind != "inbox") == 4
        assert (
            await count(models.Person) == 8 + 1
        )  # + the system bot, created when the governance seed first acts
        assert await count(models.Milestone) == 4
        assert await count(models.Risk) == 4
        assert await count(models.RiskLink) == 6
        assert await count(models.Team) == 4 and await count(models.Product) == 2
        keys = set((await s.scalars(select(models.WorkItem.key))).all())
        assert {"WEB-101", "GOV-125", "WEB-120", "DATA-80"} <= keys
        item = await s.scalar(select(models.WorkItem).where(models.WorkItem.key == "WEB-101"))
        assert item is not None and item.path.count(".") == 1  # nested under its epic


async def test_default_weight_scheme_reproduces_the_snapshot_weights(
    seed_sf: async_sessionmaker[AsyncSession],
) -> None:
    snap = load_snapshot()
    async with seed_sf() as s:
        rows = dict((await s.execute(select(models.WorkItem.key, models.WorkItem.weight))).all())
    for it in snap["items"]:
        assert int(rows[it["key"]]) == it["weight"], it["key"]


async def test_snapshot_oracle_fields_survive(seed_sf: async_sessionmaker[AsyncSession]) -> None:
    snap = load_snapshot()
    async with seed_sf() as s:
        for it in snap["items"][:15]:
            row = await s.scalar(select(models.WorkItem).where(models.WorkItem.key == it["key"]))
            assert (
                row is not None
                and row.title == it["title"]
                and row.planned_end.isoformat() == it["planned_end"]
            )
            hours = await s.scalar(
                select(func.coalesce(func.sum(models.TimeEntry.minutes), 0)).where(
                    models.TimeEntry.work_item_id == row.id
                )
            )
            assert round((hours or 0) / 60, 1) == round(it["hours_logged"], 1)
            ev = await s.scalar(
                select(func.count())
                .select_from(models.Evidence)
                .where(models.Evidence.work_item_id == row.id)
            )
            assert ev == it["evidence_count"]


async def test_exactly_one_current_placement_per_item_and_it_matches_the_snapshot(
    seed_sf: async_sessionmaker[AsyncSession],
) -> None:
    snap = load_snapshot()
    async with seed_sf() as s:
        rows = (
            await s.execute(
                select(models.WorkItem.key, models.Stage.key, models.StagePlacement.entered_at)
                .join(models.StagePlacement, models.StagePlacement.target_id == models.WorkItem.id)
                .join(models.Stage, models.Stage.id == models.StagePlacement.stage_id)
                .where(models.StagePlacement.exited_at.is_(None))
            )
        ).all()
    current = {k: (st, at) for k, st, at in rows}
    assert len(current) == 42
    for it in snap["items"]:
        assert current[it["key"]][0] == it["stage"], it["key"]


async def test_exclusion_constraint_rejects_a_second_current_placement(
    seed_sf: async_sessionmaker[AsyncSession],
) -> None:
    import pytest

    async with seed_sf() as s:
        existing = (await s.scalars(select(models.StagePlacement).limit(1))).one()
        other = (
            await s.scalars(select(models.Stage).where(models.Stage.id != existing.stage_id).limit(1))
        ).one()
        s.add(
            models.StagePlacement(
                **StagePlacement(
                    target_type=TargetType.WORK_ITEM,
                    target_id=existing.target_id,
                    process_id=existing.process_id,
                    stage_id=other.id,
                ).model_dump()
            )
        )
        with pytest.raises(IntegrityError):
            await s.flush()


async def test_mirrors_cover_every_item(seed_sf: async_sessionmaker[AsyncSession]) -> None:
    """Acceptance: builtin dimensions exist and classifications mirror project/stage/team/priority of every item."""
    async with seed_sf() as s:
        rows = (
            await s.execute(
                select(models.Dimension.key, func.count())
                .join(models.Classification, models.Classification.dimension_id == models.Dimension.id)
                .where(
                    models.Classification.target_type == TargetType.WORK_ITEM,
                    models.Classification.deleted_at.is_(None),
                )
                .group_by(models.Dimension.key)
            )
        ).all()
        n_items = await s.scalar(select(func.count()).select_from(models.WorkItem))
    by_dim = dict(rows)
    assert {"project", "team", "priority", "type"} <= set(by_dim)
    assert by_dim["stage"] == 42  # the 7 epics have no stage
    for dim in ("project", "team", "priority", "type"):
        assert by_dim[dim] == n_items, dim
    assert sum(by_dim[d] for d in ("project", "team", "priority", "stage")) >= 4 * 42


async def test_context_pack_for_mahmoud_in_the_seed(seed_sf: async_sessionmaker[AsyncSession]) -> None:
    """Phase 2 acceptance: all four projects, items sorted by recency, under 30 KB (stages/risks now filled by Phase 3)."""
    from tt_server.domain.contextpack import build_pack

    async with seed_sf() as s:
        ws = await s.scalar(select(models.Workspace).where(models.Workspace.slug == "acme"))
        member = await s.scalar(
            select(models.WorkspaceMember).where(models.WorkspaceMember.handle == "@mahmoud")
        )
        assert ws and member
        pack = await build_pack(s, ws, member)
    assert {p["key"] for p in pack["projects"]} == {"WEB", "MOB", "DATA", "GOV"}
    assert len(json.dumps(pack)) < 30_000
    assert pack["my_open_items"] and all(i["key"] and i["title"] for i in pack["my_open_items"])
    assert {m["handle"] for m in pack["people"]} >= {"@sara", "@lina"}
    assert pack["truncated"] is False
    assert pack["stages"]["sw-delivery"][0] == "idea" and {r["key"] for r in pack["risks"]} >= {"GOV-R2"}
    data = next(p for p in pack["projects"] if p["key"] == "DATA")
    assert data["process"] == "sw-delivery" and data["default_stage"] == "discovery"
