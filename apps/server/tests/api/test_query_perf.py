"""POST /query/items p95 < 300 ms on 50 k items / ~200 k classifications (Phase 6 acceptance)."""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m

from tests.api.conftest import Api

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.seed.load import load_items  # noqa: E402

ROUTERS_COVERED = ("query",)
N = 50_000
BUDGET_MS = 300.0

FILTERS = {
    "stage+priority": {"dims": {"stage": {"in": ["build", "test"]}}, "priority": {"gte": "medium"}},
    "dimension": {"dims": {"project": {"in": ["WEB"]}}, "state": {"category": ["open", "active"]}},
    "text": {"text": "checkout"},
    "tags+or": {"or": [{"tags": {"any": ["bug"]}}, {"priority": {"gte": "high"}}]},
}


async def test_items_query_budget_on_fifty_thousand_items(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tpl = (await api.call("POST", "/items", json={"title": "template", "project": "WEB"})).json()["key"]
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    async with session_factory() as s:
        await load_items(s, tpl, N)
        assert (await s.scalar(select(func.count()).select_from(m.WorkItem))) > N
        n_cls = await s.scalar(select(func.count()).select_from(m.Classification))
        assert n_cls and n_cls >= N * 3
        await s.execute(text("ANALYZE"))
        await s.commit()
    report = {}
    for who in ("@mahmoud", "@sara"):
        for name, flt in FILTERS.items():
            times = []
            for _ in range(12):
                t0 = time.perf_counter()
                r = await api.call(
                    "POST", "/query/items", as_=who, json={"filter": flt, "page": {"size": 50}}
                )
                times.append((time.perf_counter() - t0) * 1000)
                assert r.status_code == 200, r.text
            times.sort()
            report[(who, name)] = times[int(len(times) * 0.95) - 1]
    print("PERF", {f"{k[0]}:{k[1]}": round(v) for k, v in report.items()})
    assert max(report.values()) < BUDGET_MS, report
    assert statistics.median(report.values()) < BUDGET_MS / 2
