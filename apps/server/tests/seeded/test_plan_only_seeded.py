"""Plan P3 §3.13: the plan-only project ERP (zero tasks) must not break any list, board, pipeline, snapshot or deck."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.api.conftest import Api

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools.seed.demo import persist_plan_only  # noqa: E402


@pytest.fixture
async def erp(seed_sf: async_sessionmaker[AsyncSession], sapi: Api) -> Api:
    assert await persist_plan_only(seed_sf) == "ERP"
    return sapi


async def test_the_plan_tree_is_complete_with_no_work_reported(erp: Api) -> None:
    r = await erp.call("GET", "/projects/ERP/plan", params={"as_of": "2026-10-23"})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["counts"] == {"phases": 3, "deliverables": 9, "milestones": 4}
    assert len(plan["dependencies"]) == 2
    deliverables = [d for p in plan["phases"] for d in p["deliverables"]]
    assert all(d["task_count"] == 0 and d["progress_pct"] is None for d in deliverables)
    # the seed "today" is 2026-10-23: the first gate (09-30) is past, the rest are ahead
    assert {x["name"]: x["status"] for x in plan["milestones"]} == {
        "Kick-off complete": "overdue", "Finance design signed off": "planned", "UAT exit": "planned", "Go-live": "planned",
    }  # fmt: skip
    assert plan["project"]["baseline_name"] == "approved"
    early = (await erp.call("GET", "/projects/ERP/plan", params={"as_of": "2026-08-01"})).json()
    assert {d["status"] for p in early["phases"] for d in p["deliverables"]} == {"not_started"}
    assert {x["status"] for x in early["milestones"]} <= {"planned", "due_soon"}


async def test_every_list_board_and_layout_endpoint_copes(erp: Api) -> None:
    f = {"filter": {"project": {"in": ["ERP"]}}}
    ok: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", "/projects", {}), ("GET", "/projects/ERP/board", {}), ("GET", "/processes/sw-delivery/pipeline", {}),
        ("GET", "/items", {}), ("GET", "/milestones", {}), ("GET", "/snapshot", {"params": {"fresh": 1}}),
        ("GET", "/snapshot", {"params": {"scope": "project:ERP"}}), ("POST", "/query/items", {"json": f}),
        ("POST", "/query/items:csv", {"json": f}), ("POST", "/query/gantt", {"json": {**f, "rows": "item"}}),
        ("POST", "/query/aggregate", {"json": {**f, "rows": "stage", "measure": "count"}}),
        ("GET", "/now", {}), ("GET", "/violations", {}), ("GET", "/habits", {}),
    ]  # fmt: skip
    for method, path, kw in ok:
        r = await erp.call(method, path, **kw)
        assert r.status_code < 500 and r.status_code in (200, 201, 400), (
            method,
            path,
            r.status_code,
            r.text[:200],
        )
    snap = (await erp.call("GET", "/snapshot", params={"scope": "project:ERP"})).json()
    assert {i["project"] for i in snap["items"]} <= {"ERP"}


async def test_a_deck_over_the_plan_only_project_renders_and_exports(erp: Api) -> None:
    p = (
        await erp.call(
            "POST",
            "/presentations:from-template",
            json={"template": "portfolio_review", "scope": "project:ERP"},
        )
    ).json()
    assert "id" in p, p
    data = await erp.call("GET", f"/presentations/{p['id']}/data")
    assert data.status_code == 200, data.text
    for fmt in ("html", "csv", "pptx"):
        r = await erp.call("POST", f"/presentations/{p['id']}/render", params={"format": fmt})
        assert r.status_code == 200, (fmt, r.status_code, r.text[:200])


async def test_a_task_captured_into_erp_is_parented_or_asked(erp: Api) -> None:
    res = await erp.send(action="create", item={"title": "configure the GL module"}, project="ERP")
    link = res["resolution"]["plan_link"]
    assert link == "linked:ERP-" + link.split("-")[-1] or link == "asked", link
    if link == "asked":
        rows = (await erp.call("GET", "/review-queue", params={"kind": "unlinked_task"})).json()["data"]
        assert rows and rows[0]["suggestion"]["deliverables"][0]["title"] == "General ledger configuration"
    else:
        plan = (await erp.call("GET", "/projects/ERP/plan", params={"as_of": "2026-10-23"})).json()
        gl = next(
            d
            for p in plan["phases"]
            for d in p["deliverables"]
            if d["title"] == "General ledger configuration"
        )
        assert gl["task_count"] == 1
