"""Visibility filter cost on a 10 k-item project (Phase 4 acceptance: p95 < 150 ms; router: core)."""

from __future__ import annotations

import statistics
import time

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.acl.context import load_context
from tt_server.acl.visibility import visible
from tt_server.db import models as m

from tests.api.conftest import Api

ROUTERS_COVERED = ("core",)
pytestmark = pytest.mark.perf
N = 10_000
BUDGET_MS = 150.0


async def _bulk_items(s: AsyncSession, template_key: str) -> None:
    cols = [
        c.name
        for c in m.WorkItem.__table__.columns
        if c.name not in {"id", "key", "path", "title", "parent_id", "template_item_id"}
    ]
    names = ", ".join(cols)
    sel = ", ".join(f"w.{c}" for c in cols)
    await s.execute(
        text(
            f"""
            INSERT INTO work_items (id, key, path, title, {names})
            SELECT gen_random_uuid()::text, 'PERF-' || g, text2ltree('perf' || g), 'perf item ' || g, {sel}
            FROM work_items w, generate_series(1, {N}) g WHERE w.key = :k
            """
        ),
        {"k": template_key},
    )
    await s.commit()


async def _p95(api: Api, as_: str, params: dict[str, str]) -> tuple[float, int]:
    times: list[float] = []
    count = 0
    for _ in range(15):
        t0 = time.perf_counter()
        r = await api.call("GET", "/items", as_=as_, params=params)
        times.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
        count = len(r.json()["data"])
    times.sort()
    return times[int(len(times) * 0.95) - 1], count


async def test_visibility_filter_on_ten_thousand_items(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    key = (await api.call("POST", "/items", json={"title": "template", "project": "WEB"})).json()["key"]
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    async with session_factory() as s:
        await _bulk_items(s, key)
        total = await s.scalar(select(func.count()).select_from(m.WorkItem))
        assert total is not None and total > N
        ws_id = await s.scalar(select(m.Workspace.id))
        sara = await s.scalar(select(m.WorkspaceMember.person_id).where(m.WorkspaceMember.handle == "@sara"))
        assert ws_id and sara
        ctx = await load_context(s, ws_id, sara)
        visible_count = await s.scalar(visible(select(func.count()).select_from(m.WorkItem), ctx, m.WorkItem))
        assert visible_count == total  # sara is a WEB member
        await s.execute(text("ANALYZE work_items"))
        plan = "\n".join(
            row[0]
            for row in (
                await s.execute(
                    text(
                        "EXPLAIN SELECT id FROM work_items WHERE project_id IS NOT NULL ORDER BY id LIMIT 200"
                    )
                )
            ).all()
        )
        assert "Limit" in plan
    p95_member, n_member = await _p95(api, "@sara", {"limit": "200"})
    p95_admin, n_admin = await _p95(api, "@mahmoud", {"limit": "200"})
    p95_other, n_other = await _p95(
        api, "@ali", {"limit": "200"}
    )  # sees nothing: the predicate must prune fast
    assert (n_member, n_admin, n_other) == (200, 200, 0)
    assert statistics.median([p95_member, p95_admin]) < BUDGET_MS, (p95_member, p95_admin)
    assert p95_other < BUDGET_MS
