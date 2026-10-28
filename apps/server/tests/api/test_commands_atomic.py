"""Phase 14 review M1/M3: a command is one transaction (a failure midway leaves nothing) and its events carry the correlation id."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.api.routers import core, core_tenancy, overlay
from tt_server.db import models as m
from tt_server.domain.errors import Invalid

from tests.api.conftest import Api
from tests.api.test_commands import cmd, one, send

ROUTERS_COVERED = ("commands",)


def _fail_on_nth(real: Any, n: int) -> Any:
    """Wrap an async router function: the n-th call (1-based) raises a domain error after the earlier calls really wrote."""
    calls = {"n": 0}

    async def wrapper(*a: Any, **kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == n:
            raise Invalid("boom", "simulated failure midway")
        return await real(*a, **kw)

    return wrapper


async def _count(sf: async_sessionmaker[AsyncSession], model: Any, *where: Any) -> int:
    async with sf() as s:
        return int(await s.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def test_team_create_failing_on_the_second_membership_leaves_no_team(api: Api, session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.setattr(core_tenancy, "add_membership", _fail_on_nth(core_tenancy.add_membership, 2))
    c = cmd("team.create", {"name": "atomic-team", "kind": "virtual", "members": ["@sara", "@ali"]})
    res = (await send(api, c))[0]
    assert res["status"] == "rejected" and res["errors"][0]["code"] == "boom", res
    assert await _count(session_factory, m.Team, m.Team.slug == "atomic-team") == 0
    assert await _count(session_factory, m.Event, m.Event.event_type.in_(["team.created", "command.applied"])) == 0
    assert await _count(session_factory, m.CommandLog, m.CommandLog.client_id == c["client_id"]) == 0
    monkeypatch.undo()
    retry = (await send(api, c))[0]  # the same client_id is not stuck behind a half-applied first attempt
    assert retry["status"] == "applied" and retry.get("deduplicated") is None, retry


async def test_team_add_members_failing_midway_adds_nobody(api: Api, session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    team = (await one(api, "team.create", {"name": "grp", "kind": "virtual"}))["result"]
    monkeypatch.setattr(core_tenancy, "add_membership", _fail_on_nth(core_tenancy.add_membership, 2))
    res = await one(api, "team.add_members", {"team": "grp", "members": ["@sara", "@ali"]})
    assert res["status"] == "rejected", res
    assert await _count(session_factory, m.Membership, m.Membership.team_id == team["id"]) == 0


async def test_item_reparent_failing_on_the_second_item_moves_none(api: Api, session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    parent = (await one(api, "item.create", {"title": "p", "project": "WEB", "type": "epic"}))["result"]
    kids = [(await one(api, "item.create", {"title": f"k{i}", "project": "WEB"}))["result"] for i in range(2)]
    monkeypatch.setattr(core, "move", _fail_on_nth(core.move, 2))
    res = await one(api, "item.reparent", {"keys": [k["key"] for k in kids], "parent": parent["key"]}, confirmed=True)  # fmt: skip
    assert res["status"] == "rejected", res
    assert await _count(session_factory, m.WorkItem, m.WorkItem.parent_id == parent["id"]) == 0


async def test_stage_move_failing_on_the_second_item_moves_none(api: Api, session_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    keys = [(await one(api, "item.create", {"title": f"s{i}", "project": "WEB"}))["result"]["key"] for i in range(2)]  # fmt: skip
    control = (await one(api, "item.create", {"title": "ctl", "project": "WEB"}))["result"]["key"]
    assert (await one(api, "stage.move", {"keys": [control], "stage": "build"}))["status"] == "applied"  # the stage exists
    before = await _count(session_factory, m.Event, m.Event.event_type == "stage.moved")
    monkeypatch.setattr(overlay, "move_stage", _fail_on_nth(overlay.move_stage, 2))
    res = await one(api, "stage.move", {"keys": keys, "stage": "build"})
    assert res["status"] == "rejected", res
    assert await _count(session_factory, m.Event, m.Event.event_type == "stage.moved") == before


async def test_command_events_carry_the_batch_correlation_id(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    cmds = [cmd("tag.create", {"name": "corr-a"}), cmd("team.create", {"name": "corr-team", "members": ["@sara"]})]
    r = await api.call("POST", "/commands", json={"workspace": api.slug, "commands": cmds})
    assert r.status_code == 200, r.text
    async with session_factory() as s:
        evs = (await s.scalars(select(m.Event).where(m.Event.payload["command"].is_not(None)))).all()
    assert evs
    ids = {e.correlation_id for e in evs}
    assert None not in ids, [(e.event_type, e.correlation_id) for e in evs]
    assert len(ids) == 1  # one batch, one correlation id
