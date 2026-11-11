"""ADR-0056 amendment: a rejected command is audited (a `command_log` row with status `rejected` + a `command.rejected` event),
but it never blocks a corrected retry with the same client_id, and a preview is never logged."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m

from tests.api.conftest import Api
from tests.api.test_commands import cmd, code, send

ROUTERS_COVERED = ("commands",)


async def rows(sf: async_sessionmaker[AsyncSession], client_id: str) -> list[m.CommandLog]:
    async with sf() as s:
        return list((await s.scalars(select(m.CommandLog).where(m.CommandLog.client_id == client_id))).all())


async def test_a_rejected_command_leaves_an_audit_row_and_event(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    c = cmd("project.create", {"name": "Nope", "key": "NOPE"}, actor="@ali")
    res = (await send(api, c, as_="@ali"))[0]
    assert code(res) == "forbidden"
    (row,) = await rows(session_factory, c["client_id"])
    assert row.status == "rejected" and row.op == "project.create"
    assert row.result["errors"][0]["code"] == "forbidden"
    async with session_factory() as s:
        ali = await s.scalar(select(m.WorkspaceMember.person_id).where(m.WorkspaceMember.handle == "@ali"))
        evs = (await s.scalars(select(m.Event).where(m.Event.event_type == "command.rejected"))).all()
    assert row.actor_id == ali  # the authenticated actor, whatever the command claimed
    assert len(evs) == 1 and evs[0].payload["after"] == {"op": "project.create", "client_id": c["client_id"], "status": "rejected", "codes": ["forbidden"]}  # fmt: skip
    assert "args" not in evs[0].payload["after"]  # the audit says what was refused, not the content


async def test_a_corrected_retry_with_the_same_client_id_is_evaluated_and_replaces_the_rejection(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    bad = cmd("tag.create", {})  # missing name -> invalid
    assert code((await send(api, bad))[0]) == "invalid"
    (row,) = await rows(session_factory, bad["client_id"])
    assert row.status == "rejected"
    fixed = {**bad, "args": {"name": "corrected", "_meta": bad["args"]["_meta"]}}
    ok = (await send(api, fixed))[0]
    assert ok["status"] == "applied" and not ok.get("deduplicated"), ok  # not answered from the rejection
    (row,) = await rows(session_factory, bad["client_id"])
    assert (
        row.status == "applied" and row.result["status"] == "applied"
    )  # one row per client_id, now the applied one
    again = (await send(api, fixed))[0]
    assert again["status"] == "applied" and again["deduplicated"] is True  # only an applied command dedupes


async def test_rejecting_the_same_client_id_twice_keeps_one_row(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    bad = cmd("tag.create", {})
    for _ in range(2):
        assert code((await send(api, bad))[0]) == "invalid"
    assert len(await rows(session_factory, bad["client_id"])) == 1


async def test_a_preview_is_never_logged_even_when_rejected(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    p = cmd("project.create", {"name": "x"}, preview=True)  # invalid args
    assert code((await send(api, p))[0]) == "invalid"
    ok = cmd("project.create", {"name": "Prev", "key": "PRV"}, preview=True)
    assert (await send(api, ok))[0]["status"] == "previewed"
    assert (
        await rows(session_factory, p["client_id"]) == []
        and await rows(session_factory, ok["client_id"]) == []
    )


async def test_a_client_id_used_by_someone_else_is_refused_not_answered_with_their_result(api: Api) -> None:  # fmt: skip
    mine = cmd("tag.create", {"name": "mine"})
    assert (await send(api, mine))[0]["status"] == "applied"
    theirs = {**cmd("tag.create", {"name": "theirs"}, actor="@ali"), "client_id": mine["client_id"]}
    res = (await send(api, theirs, as_="@ali"))[0]
    assert code(res) == "client_id_in_use" and "mine" not in str(
        res
    )  # never the other person's stored result


async def test_rejections_inside_a_capture_batch_are_logged_too(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    bad = cmd("project.create", {"name": "Nope", "key": "NOPE"}, actor="@ali")
    msg = {"actor": "@ali", "action": "create", "at": "2026-10-25T10:00:00+03:00", "client_id": str(uuid.uuid4()), "project": "WEB", "item": {"title": "x"}}  # fmt: skip
    r = await api.call("POST", "/capture/batches", as_="@ali", json={"workspace": "acme", "messages": [msg], "commands": [bad]})  # fmt: skip
    assert r.status_code == 207, r.text
    (row,) = await rows(session_factory, bad["client_id"])
    assert row.status == "rejected"
