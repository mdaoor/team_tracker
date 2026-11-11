"""Phase 12 §4: retention purge, personal-data export (self vs admin) and erasure (router: privacy)."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import utcnow
from tt_server.db import models as m
from tt_server.domain import privacy

from tests.api.conftest import Api

ROUTERS_COVERED = ("privacy",)


async def sara_has_data(api: Api) -> None:
    await api.role("@sara", "manager")  # may create items and comment
    await api.send(
        as_="@sara",
        action="note",
        note="private-ish note",
        raw_input="sara typed: private-ish note",
        project="WEB",
    )
    r = await api.call("POST", "/items", as_="@sara", json={"title": "sara task", "project": "WEB"})
    await api.call("POST", f"/items/{r.json()['key']}/comments", as_="@sara", json={"body": "my comment"})


async def test_purge_nulls_only_old_raw_input_and_keeps_the_event(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await sara_has_data(api)
    async with session_factory() as s:
        ev = (await s.scalars(select(m.Event).where(m.Event.raw_input.is_not(None)))).first()
        assert ev
        await s.execute(
            update(m.Event).where(m.Event.id == ev.id).values(received_at=utcnow() - timedelta(days=100))
        )
        await s.commit()
        n_before = len((await s.scalars(select(m.Event).where(m.Event.raw_input.is_not(None)))).all())
    r = await api.call("POST", "/retention:purge-raw-input", json={"days": 30})
    assert r.status_code == 200 and r.json()["purged"] == 1
    async with session_factory() as s:
        assert await s.get(m.Event, ev.id) is not None  # the structured event stays
        assert (
            len((await s.scalars(select(m.Event).where(m.Event.raw_input.is_not(None)))).all())
            == n_before - 1
        )
    assert (await api.call("POST", "/retention:purge-raw-input", json={"days": 0})).status_code in (400, 422)
    assert (
        await api.call("POST", "/retention:purge-raw-input", as_="@sara", json={"days": 30})
    ).status_code in (403, 404)


async def test_workspace_retention_setting_drives_the_nightly_job(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await sara_has_data(api)
    async with session_factory() as s:
        await s.execute(update(m.Event).values(received_at=utcnow() - timedelta(days=40)))
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws
        assert await privacy.purge_all(session_factory) == 0  # no setting: keep forever
        ws.settings = {**ws.settings, "raw_input_retention_days": 30}
        await s.commit()
    assert await privacy.purge_all(session_factory) >= 1


async def test_self_export_is_complete_and_admin_export_excludes_private_data(api: Api) -> None:
    await sara_has_data(api)
    mine = await api.call("GET", "/me/export", as_="@sara")
    assert mine.status_code == 200 and "attachment" in mine.headers["content-disposition"]
    doc = mine.json()
    assert doc["person"]["email"] == "sara@acme.test" and doc["scope"].startswith("full")
    assert any(i["title"] == "sara task" for i in doc["items"]) and doc["comments"][0]["body"] == "my comment"
    assert any(e["raw_input"] == "sara typed: private-ish note" for e in doc["events"])
    adm = (await api.call("GET", "/people/@sara/export")).json()
    assert adm["scope"].startswith("administrative") and any(i["title"] == "sara task" for i in adm["items"])
    assert (
        (await api.call("GET", "/people/@mahmoud/export")).json()["scope"].startswith("full")
    )  # an admin exporting themself
    assert (await api.call("GET", "/people/@sara/export", as_="@ali")).status_code in (403, 404)
    assert (await api.call("GET", "/people/@ghost/export")).status_code == 404


async def test_erase_anonymises_and_revokes_but_keeps_the_audit_trail(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await sara_has_data(api)
    tok = await api.c.post(
        "/auth/tokens",
        headers={"X-Debug-Actor": "@sara"},
        json={"workspace": "acme", "name": "cli", "scopes": ["capture"]},
    )
    assert tok.status_code == 201, tok.text
    assert (await api.call("POST", "/people/@sara:erase", json={"confirm": "@wrong"})).status_code == 400
    assert (
        await api.call("POST", "/people/@mahmoud:erase", json={"confirm": "@mahmoud"})
    ).status_code == 400  # not yourself
    r = await api.call("POST", "/people/@sara:erase", json={"confirm": "@sara"})
    assert r.status_code == 200 and r.json()["comments"] == 1 and r.json()["tokens"] >= 1
    async with session_factory() as s:
        p = (await s.scalars(select(m.Person).where(m.Person.id == r.json()["erased"]))).one()
        assert p.display_name == "Erased user" and "sara" not in p.email and p.deleted_at is not None
        assert [
            c.body for c in (await s.scalars(select(m.Comment).where(m.Comment.author_id == p.id))).all()
        ] == ["[erased]"]
        assert (
            await s.scalars(select(m.Event).where(m.Event.actor_id == p.id, m.Event.raw_input.is_not(None)))
        ).first() is None
        assert (
            await s.scalars(select(m.Event).where(m.Event.actor_id == p.id))
        ).first() is not None  # audit trail kept
        assert (
            await s.scalars(select(m.Event).where(m.Event.event_type == "person.erased"))
        ).first() is not None
    assert (await api.call("GET", "/me", as_="@sara")).status_code in (401, 404)
    assert "@sara" not in [p["handle"] for p in (await api.call("GET", "/people")).json()["data"]]
