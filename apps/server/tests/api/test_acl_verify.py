"""ADR-0040: `verify-projection` finds a projected fact that no event explains."""

from __future__ import annotations

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.domain import verify

from tests.api.conftest import Api

ROUTERS_COVERED = ("core",)


async def ws_id(sf: async_sessionmaker[AsyncSession]) -> str:
    async with sf() as s:
        from sqlalchemy import select

        return str(await s.scalar(select(m.Workspace.id).where(m.Workspace.slug == "acme")))


async def test_a_workspace_built_through_the_api_is_clean(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.send(action="log_time", item={"title": "write docs"}, project="WEB", minutes=45)
    await api.call("POST", "/items", json={"title": "another", "project": "WEB"})
    async with session_factory() as s:
        rep = await verify.verify(s, await ws_id(session_factory))
    assert rep.clean, rep.findings


async def test_missing_events_and_tampered_minutes_are_reported(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.send(action="log_time", item={"title": "write docs"}, project="WEB", minutes=45)
    made = (await api.call("POST", "/items", json={"title": "another", "project": "WEB"})).json()
    async with session_factory() as s:
        await s.execute(
            delete(m.Event).where(m.Event.entity_id == made["id"], m.Event.event_type == "item.created")
        )
        await s.execute(update(m.TimeEntry).values(minutes=90))
        await s.commit()
        rep = await verify.verify(s, await ws_id(session_factory))
    assert not rep.clean
    assert made["key"] in rep.findings["items_without_created_event"]
    assert rep.findings["time_entry_minutes_differ_from_event"]
