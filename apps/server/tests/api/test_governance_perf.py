"""Phase 8 performance and the candidate-filter pushdown: the filter is SQL, and a 50 k-placement sweep is bounded."""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.governance import engine

from tests.api.conftest import Api
from tests.api.test_acl_governance import make_policy, run

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
import pytest as _pytest

from tools.seed.load import load_items  # noqa: E402

pytestmark = _pytest.mark.timeout(900)  # 50 k-item perf scenarios

ROUTERS_COVERED = ("governance",)
N = 50_000


async def test_the_candidate_filter_and_scope_are_pushed_into_sql(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await make_policy(
        api,
        key="pushed",
        scope_type="project",
        scope_id="WEB",
        candidate_filter={"priority": {"gte": "high"}, "tags": {"any": ["bug"]}},
    )
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        pol = await s.scalar(select(m.Policy).where(m.Policy.key == "pushed"))
        assert ws and pol
        stmt = await engine.candidate_statement(s, ws, pol, datetime.now(UTC))
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "work_items.project_id" in sql and "tag_links" in sql and "priority" in sql.lower()
        plan = "\n".join(r[0] for r in (await s.execute(text("EXPLAIN " + sql))).all())
        assert "tag_links" in plan and ("Filter" in plan or "Cond" in plan)  # the database evaluates it


async def test_a_stuck_in_stage_sweep_over_fifty_thousand_placements_stays_bounded(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tpl = (await api.call("POST", "/items", json={"title": "template", "project": "WEB"})).json()["key"]
    async with session_factory() as s:
        await load_items(s, tpl, N)
        await s.execute(text("ANALYZE"))
        await s.commit()
    r = await api.call(
        "POST",
        "/policies",
        json={"key": "stuck", "name": "stuck", "template": "stuck_in_stage", "params": {"days": 1}},
    )
    assert r.status_code == 201, r.text
    assert (await api.call("POST", "/policies/stuck:test")).status_code == 200
    assert (await api.call("POST", "/policies/stuck:activate")).status_code == 200
    t0 = time.perf_counter()
    out = await run(api, "stuck", datetime.now(UTC) + timedelta(days=60))
    took = time.perf_counter() - t0
    print(
        "GOV PERF",
        {
            "sweep_s": round(took, 1),
            "matched": out["matched"],
            "opened": out["opened"],
            "errors": out["errors"],
        },
    )
    assert out["errors"] == [] and out["opened"] == out["matched"] and out["matched"] > 40_000
    assert took < 400, (
        took
    )  # worst case: every one of 50k placements opens a violation + event (measured ~180 s)
    again_t0 = time.perf_counter()
    again = await run(api, "stuck", datetime.now(UTC) + timedelta(days=60))
    assert again["opened"] == 0 and again["auto_resolved"] == 0 and again["matched"] == out["matched"]
    steady = time.perf_counter() - again_t0
    print("GOV PERF steady-state sweep", round(steady, 1))
    assert steady < 150, (
        steady
    )  # nothing new to open: ~50k celpy evaluations + diff (measured ~77 s, about 1.5 ms/row)
