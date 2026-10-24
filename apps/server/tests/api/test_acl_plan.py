"""Phase 1 §1.2 top-down plan: create in one transaction, read the tree with derived statuses (router: plan)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m

from tests.api.conftest import Api

ROUTERS_COVERED = ("plan",)
TODAY = "2026-10-24"

PLAN: dict[str, Any] = {
    "phases": [
        {"title": "Discovery", "planned_start": "2026-09-01", "planned_end": "2026-10-15", "deliverables": [
            {"ref": "req", "title": "Requirements", "planned_start": "2026-09-01", "planned_end": "2026-09-30", "milestone": "Kick-off done", "owner": "@sara"},
            {"ref": "arch", "title": "Architecture", "planned_start": "2026-10-01", "planned_end": "2026-10-20", "depends_on": ["req"]},
        ]},
        {"title": "Build", "planned_start": "2026-10-21", "planned_end": "2026-12-31", "deliverables": [
            {"ref": "mvp", "title": "MVP", "planned_start": "2026-10-21", "planned_end": "2026-12-01", "milestone": "Go-live", "depends_on": ["arch"], "lag_days": 2},
            {"title": "Integrations", "planned_start": "2026-11-01", "planned_end": "2026-12-31"},
        ]},
    ],
    "milestones": [
        {"name": "Kick-off done", "kind": "gate", "due_date": "2026-09-30"},
        {"name": "Go-live", "kind": "release", "due_date": "2026-12-15", "description": "production"},
    ],
}  # fmt: skip


async def make(api: Api, key: str = "ERP") -> None:
    r = await api.call("POST", "/projects", json={"name": "ERP rollout", "key": key})
    assert r.status_code == 201, r.text
    r = await api.call("POST", f"/projects/{key}/plan", json=PLAN)
    assert r.status_code == 201, r.text


async def read(api: Api, key: str = "ERP", as_of: str = TODAY, **kw: Any) -> dict[str, Any]:
    r = await api.call("GET", f"/projects/{key}/plan", params={"as_of": as_of}, **kw)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def deliv(plan: dict[str, Any], title: str) -> dict[str, Any]:
    return next(d for p in plan["phases"] for d in p["deliverables"] if d["title"] == title)


async def test_a_plan_is_created_in_one_transaction_with_an_event(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.call("POST", "/projects", json={"name": "ERP rollout", "key": "ERP"})
    r = await api.call("POST", "/projects/ERP/plan", json=PLAN)
    assert r.json() == {"phases": 2, "items": 6, "milestones": 2, "dependencies": 2}
    async with session_factory() as s:
        ev = (await s.scalars(select(m.Event).where(m.Event.event_type == "plan.created"))).all()
        n = await s.scalar(
            select(func.count()).select_from(m.WorkItem).join(m.Project).where(m.Project.key == "ERP")
        )
    assert len(ev) == 1 and ev[0].payload["after"]["items"] == 6 and n == 6


async def test_a_project_planned_with_no_work_returns_a_full_tree(api: Api) -> None:
    await make(api)
    plan = await read(api)
    assert [p["title"] for p in plan["phases"]] == ["Discovery", "Build"]
    assert plan["counts"] == {"phases": 2, "deliverables": 4, "milestones": 2}
    d = deliv(plan, "MVP")
    assert d["task_count"] == 0 and d["progress_pct"] is None and d["depends_on"]
    assert all(p["progress_pct"] is None and p["task_count"] == 0 for p in plan["phases"])
    # nothing reported, nothing due yet at an early date: every status is not_started
    early = await read(api, as_of="2026-08-01")
    assert {d["status"] for p in early["phases"] for d in p["deliverables"]} == {"not_started"}
    assert early["project"]["status"] == "not_started" and early["project"]["planned_end"].startswith(
        "2026-12-31"
    )


async def test_milestones_carry_kind_status_and_counts(api: Api) -> None:
    await make(api)
    plan = await read(api)
    ms = {x["name"]: x for x in plan["milestones"]}
    assert ms["Kick-off done"]["kind"] == "gate" and ms["Kick-off done"]["status"] == "overdue"
    assert ms["Go-live"]["kind"] == "release" and ms["Go-live"]["status"] == "planned"
    assert ms["Kick-off done"]["deliverables_total"] == 1 and ms["Kick-off done"]["deliverables_done"] == 0
    assert plan["project"]["health"] == "red"  # an overdue milestone
    soon = await read(api, as_of="2026-12-05")
    assert {x["name"]: x["status"] for x in soon["milestones"]}["Go-live"] == "due_soon"


async def test_statuses_follow_dates_and_work(api: Api) -> None:
    await make(api)
    plan = await read(api)
    assert deliv(plan, "Requirements")["status"] == "overdue"  # ended 09-30, not done
    assert deliv(plan, "MVP")["status"] == "at_risk"  # its predecessor chain is late
    assert deliv(plan, "Integrations")["status"] == "not_started"
    req = deliv(plan, "Requirements")
    # a reporter logs a task under the deliverable
    t = await api.call(
        "POST",
        "/items",
        json={"title": "interview users", "project": "ERP", "parent": req["key"], "type_id": None},
    )
    assert t.status_code == 201, t.text
    assert deliv(await read(api), "Integrations")["status"] == "not_started"
    integ = deliv(plan, "Integrations")
    t2 = await api.call(
        "POST", "/items", json={"title": "connect SAP", "project": "ERP", "parent": integ["key"]}
    )
    assert t2.status_code == 201, t2.text
    after = await read(api)
    assert (
        deliv(after, "Integrations")["status"] == "in_progress"
        and deliv(after, "Integrations")["progress_pct"] == 0
    )
    await api.call("POST", f"/items/{t2.json()['key']}/state", json={"state": "done"})
    done = await read(api)
    assert deliv(done, "Integrations")["progress_pct"] == 100 and done["phases"][1]["progress_pct"] == 100
    await api.call("POST", f"/items/{integ['key']}/state", json={"state": "done"})
    assert deliv(await read(api), "Integrations")["status"] == "done"


async def test_a_late_predecessor_puts_its_successor_at_risk(api: Api) -> None:
    await make(api)
    plan = await read(
        api, as_of="2026-10-05"
    )  # Requirements (09-30) is overdue, Architecture is due in 15 days
    assert deliv(plan, "Requirements")["status"] == "overdue"
    assert (
        deliv(plan, "Architecture")["status"] == "at_risk" and deliv(plan, "MVP")["status"] == "not_started"
    )
    deps = {(d["from"], d["to"]) for d in plan["dependencies"]}
    assert len(deps) == 2 and all(d["type"] == "finish_to_start" for d in plan["dependencies"])
    assert {d["lag_days"] for d in plan["dependencies"]} == {None, 2}


async def test_baseline_variance_is_in_the_read(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from datetime import date

    await make(api)
    assert (await read(api))["project"]["baseline_name"] is None
    assert (await api.call("POST", "/projects/ERP/baselines", params={"name": "approved"})).status_code == 201
    plan = await read(api)
    assert (
        plan["project"]["baseline_name"] == "approved" and deliv(plan, "MVP")["baseline_variance_days"] == 0
    )
    key = deliv(plan, "MVP")["key"]
    async with session_factory() as s:
        it = await s.scalar(select(m.WorkItem).where(m.WorkItem.key == key))
        assert it
        it.planned_end = date(2026, 12, 6)  # five days later than the approved plan
        await s.commit()
    assert deliv(await read(api), "MVP")["baseline_variance_days"] == 5


async def test_a_bad_plan_creates_nothing(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.call("POST", "/projects", json={"name": "ERP rollout", "key": "ERP"})
    bad = {**PLAN, "phases": [{"title": "P", "deliverables": [{"title": "A", "depends_on": ["ghost"]}]}]}
    r = await api.call("POST", "/projects/ERP/plan", json=bad)
    assert r.status_code == 400 and r.json()["error"]["pointer"].endswith("/depends_on")
    for body in (
        {"phases": [{"title": "P", "planned_start": "2026-02-01", "planned_end": "2026-01-01"}]},
        {"phases": [{"title": "P", "deliverables": [{"title": "A", "owner": "@nobody"}]}]},
        {"phases": [{"title": "P", "deliverables": [{"title": "A", "milestone": "Nope"}]}]},
    ):
        assert (await api.call("POST", "/projects/ERP/plan", json=body)).status_code == 400
    async with session_factory() as s:
        n = await s.scalar(
            select(func.count()).select_from(m.WorkItem).join(m.Project).where(m.Project.key == "ERP")
        )
        ms = await s.scalar(
            select(func.count()).select_from(m.Milestone).join(m.Project).where(m.Project.key == "ERP")
        )
        ev = await s.scalar(
            select(func.count()).select_from(m.Event).where(m.Event.event_type == "plan.created")
        )
    assert (n, ms, ev) == (0, 0, 0)


async def test_plan_endpoints_are_permission_gated(api: Api) -> None:
    await make(api)
    assert (await api.call("POST", "/projects/ERP/plan", as_="@ali", json=PLAN)).status_code in (403, 404)
    assert (await api.call("GET", "/projects/ERP/plan", as_="@ali")).status_code in (403, 404)
    assert (await api.call("GET", "/projects/NOPE/plan")).status_code == 404


# ---------------------------------------------------------------- linkage (plan P3 §3.11)
async def patch_item(api: Api, key: str, body: dict[str, Any]) -> Any:
    got = await api.call("GET", f"/items/{key}")
    etag = got.headers.get("etag") or f'"{got.json()["updated_at"]}"'
    return await api.c.patch(
        api.url(f"/items/{key}"), headers={"X-Debug-Actor": "@mahmoud", "If-Match": etag}, json=body
    )


async def deliverable_key(api: Api, title: str) -> str:
    return deliv(await read(api), title)["key"]  # type: ignore[no-any-return]


async def test_a_captured_task_is_parented_under_the_deliverable_it_names(api: Api) -> None:
    await make(api)
    res = await api.send(action="create", item={"title": "interview users about requirements"}, project="ERP")
    assert res["status"] == "applied", res
    assert res["resolution"]["plan_link"].startswith("linked:"), res["resolution"]
    plan = await read(api)
    req = deliv(plan, "Requirements")
    assert req["task_count"] == 1 and req["status"] in ("in_progress", "overdue") and req["linked_tasks"] == 1
    item = (await api.call("GET", f"/items/{res['item']['key']}")).json()
    assert item["parent_id"] and res["resolution"]["plan_link"] == f"linked:{req['key']}"


async def test_an_unmatched_task_asks_with_three_suggestions_and_the_answer_links_it(api: Api) -> None:
    await make(api)
    res = await api.send(action="create", item={"title": "order team lunch"}, project="ERP")
    assert res["resolution"]["plan_link"] == "asked" and res["review_item_ids"]
    rows = (await api.call("GET", "/review-queue", params={"kind": "unlinked_task"})).json()["data"]
    assert len(rows) == 1 and len(rows[0]["suggestion"]["deliverables"]) == 3
    target = rows[0]["suggestion"]["deliverables"][0]["key"]
    assert (
        await api.call("POST", "/review-queue/resolve", json={"ref": rows[0]["ref"], "choice": {}})
    ).status_code == 400
    ok = await api.call(
        "POST", "/review-queue/resolve", json={"ref": rows[0]["ref"], "choice": {"deliverable": target}}
    )
    assert ok.status_code == 200 and ok.json()["resolved"] == f"linked:{target}"
    plan = await read(api)
    assert sum(d["task_count"] for p in plan["phases"] for d in p["deliverables"]) == 1


async def test_finishing_a_task_rolls_progress_up_deliverable_and_phase(api: Api) -> None:
    await make(api)
    a = await api.send(action="create", item={"title": "interview users about requirements"}, project="ERP")
    await api.send(action="create", item={"title": "write the requirements document"}, project="ERP")
    await api.send(action="done", item={"key": a["item"]["key"]})
    plan = await read(api)
    req = deliv(plan, "Requirements")
    assert req["task_count"] == 2 and req["tasks_done"] == 1 and req["progress_pct"] == 50
    item = (await api.call("GET", f"/items/{req['key']}")).json()
    assert item["progress_pct"] == 50 and item["progress_mode"] == "children"
    assert plan["phases"][0]["progress_pct"] == 50 and plan["phases"][1]["progress_pct"] is None


async def test_moving_a_deliverables_dates_raises_slip_and_conflict_rows_once(api: Api) -> None:
    await make(api)
    assert (await api.call("POST", "/projects/ERP/baselines", params={"name": "approved"})).status_code == 201
    plan = await read(api)
    arch = deliv(plan, "Architecture")
    moved = await patch_item(api, arch["key"], {"planned_end": "2026-11-20"})
    assert moved.status_code == 200, moved.text
    kinds = sorted(
        r["raw_kind"]
        for r in (await api.call("GET", "/review-queue")).json()["data"]
        if r["raw_kind"] in ("plan_slip", "plan_conflict")
    )
    assert kinds == [
        "plan_conflict",
        "plan_slip",
    ]  # 31 days later than the baseline; MVP starts before Architecture ends + 2 days
    await patch_item(api, arch["key"], {"planned_end": "2026-11-25"})
    again = [
        r
        for r in (await api.call("GET", "/review-queue")).json()["data"]
        if r["raw_kind"] in ("plan_slip", "plan_conflict")
    ]
    assert len(again) == 2  # idempotent: one open row per kind
