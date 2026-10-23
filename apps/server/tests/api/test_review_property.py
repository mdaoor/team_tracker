"""Property: over random reporting graphs, every review row appears in exactly one manager's queue (ADR-0006)."""

from __future__ import annotations

import random

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id
from tt_schema.enums import ProjectRole, TargetType
from tt_server.db import models as m

from tests.api.conftest import Api

HANDLES = ["@mahmoud", "@sara", "@ali", "@nour", "@omar", "@lina"]


@pytest.mark.parametrize("seed", range(6))
async def test_each_row_has_exactly_one_owner(
    api: Api, session_factory: async_sessionmaker[AsyncSession], seed: int
) -> None:
    rng = random.Random(seed)
    for h in HANDLES[3:]:
        r = await api.call(
            "POST", "/members", json={"display_name": h[1:], "email": f"{h[1:]}@acme.test", "handle": h}
        )
        assert r.status_code == 201
    pid = {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}
    # random primary line managers (no self loops), possibly none
    for h in HANDLES:
        if rng.random() < 0.7:
            mgr = rng.choice([x for x in HANDLES if x != h])
            r = await api.call(
                "POST",
                "/reporting-lines",
                json={"person_id": pid[h], "manager_id": pid[mgr], "is_primary": True},
            )
            assert r.status_code == 201
    # random extra project managers on WEB and DATA
    async with session_factory() as s:
        for key in ("WEB", "DATA"):
            proj = await s.scalar(select(m.Project).where(m.Project.key == key))
            assert proj is not None
            for h in rng.sample(HANDLES, 2):
                s.add(
                    m.ProjectMember(
                        id=new_id(), project_id=proj.id, person_id=pid[h], role=ProjectRole.MANAGER
                    )
                )
        await s.commit()
    # rows: inbox items with unresolved phrases, plus unconfirmed stage hints in real projects
    for h in HANDLES:
        await api.send(as_=h, action="create", item={"title": f"thing by {h}"}, project=f"nowhere-{h[1:]}")
        await api.send(
            as_=h,
            action="create",
            item={"title": f"staged by {h}"},
            project=rng.choice(["WEB", "DATA"]),
            stage=rng.choice(["design", "build", "test"]),
        )
    queues = {h: (await api.call("GET", "/review-queue", as_=h)).json()["data"] for h in HANDLES}
    refs = [r["ref"] for q in queues.values() for r in q]
    assert len(refs) == len(set(refs)), "a row appeared in more than one queue"
    async with session_factory() as s:
        open_reviews = await s.scalar(
            select(func.count())
            .select_from(m.ReviewItem)
            .where(m.ReviewItem.status == "open", m.ReviewItem.target_type == TargetType.WORK_ITEM.value)
        )
        unconfirmed = await s.scalar(
            select(func.count())
            .select_from(m.Classification)
            .where(m.Classification.is_confirmed.is_(False), m.Classification.deleted_at.is_(None))
        )
    assert len(refs) == (open_reviews or 0) + (unconfirmed or 0) > 0, "a row had no owner"
