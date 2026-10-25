"""Phase 15 governance: `development_hours_below` and the positive habit `learning_consistency` (ADR-0043)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.governance import facts, habits

from tests.api.conftest import Api
from tests.api.test_acl_core import pid


async def _policy(api: Api) -> None:
    r = await api.call(
        "POST",
        "/policies",
        json={
            "key": "dev_hours",
            "name": "dev hours",
            "template": "development_hours_below",
            "params": {"hours": 1},
            "trigger": "scheduled",
            "schedule": "0 8 * * 1",
            "event_types": None,
        },
    )
    assert r.status_code == 201, r.text
    assert (await api.call("POST", "/policies/dev_hours:test")).status_code == 200
    assert (await api.call("POST", "/policies/dev_hours:activate")).status_code == 200


async def test_template_and_habit_definition_are_in_the_catalogue(api: Api) -> None:
    t = {x["key"]: x for x in (await api.call("GET", "/policy-templates")).json()["data"]}
    assert (
        t["development_hours_below"]["category"] == "wellbeing"
        and t["development_hours_below"]["target"] == "person"
    )
    defs = {d["key"]: d for d in (await api.call("GET", "/habit-definitions")).json()["data"]}
    assert defs["learning_consistency"]["polarity"] == "positive"
    fns = {f["name"] for f in (await api.call("GET", "/meta/policy-context")).json()["functions"]}
    assert "development_minutes" in fns


async def test_the_policy_fires_below_the_target_and_is_visible_to_the_person_and_line_manager_only(
    api: Api,
) -> None:
    line = {"person_id": await pid(api, "@sara"), "manager_id": await pid(api, "@ali"), "kind": "line"}
    assert (await api.call("POST", "/reporting-lines", json=line)).status_code == 201
    await api.send(as_="@sara", action="log_time", item={"title": "Rust ch.3"}, project="DEV", minutes=30)
    await _policy(api)
    run = await api.call("POST", "/policies/dev_hours:run")
    assert run.status_code == 200, run.text
    assert run.json()["opened"] >= 1

    async def seen(handle: str) -> list[str]:
        r = await api.call("GET", "/violations", as_=handle, params={"category": "wellbeing"})
        assert r.status_code == 200, r.text
        return [v["target_id"] for v in r.json()["data"]]

    sara = await pid(api, "@sara")
    assert sara in await seen("@sara")
    assert sara in await seen("@ali")  # the line manager
    assert sara not in await seen(
        "@mahmoud"
    )  # another manager, even the workspace owner, never sees wellbeing


async def test_development_minutes_count_only_tagged_work_in_the_window(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.send(as_="@sara", action="log_time", item={"title": "Arabic course"}, project="DEV", minutes=45)
    await api.send(as_="@sara", action="log_time", item={"title": "standup"}, project="WEB", minutes=15)
    sara = await pid(api, "@sara")
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws is not None
        f = await facts.load(
            s,
            ws.id,
            {"development"},
            now=datetime.now(UTC) + timedelta(days=1),
            item_ids=[],
            person_ids=[sara],
            item_rows={},
            windows={"7d"},
            project_keys=[],
        )
    assert f.development == {(sara, "7d"): 45}


async def test_learning_consistency_counts_weeks_with_development_time(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.send(as_="@sara", action="log_time", item={"title": "Rust"}, project="DEV", minutes=30)
    sara = await pid(api, "@sara")
    now = datetime.now(UTC)
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws is not None
        out = await habits.d_learning_consistency(s, ws.id, now - timedelta(days=27), now + timedelta(days=1))
    assert (out[sara].occ, out[sara].opp) == (1, 4)
    other = [c for p, c in out.items() if p != sara]
    assert other and all(c.occ == 0 and c.opp == 4 for c in other)
