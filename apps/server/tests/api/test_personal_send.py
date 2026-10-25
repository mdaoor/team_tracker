"""Phase 15 server side: calendar dedup (ADR-0042), time.unlog, the development project (ADR-0043), ContextPack hints."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.enums import TargetType
from tt_server.db import models as m

from tests.api.conftest import Api
from tests.api.test_acl_core import pid

CAL = {
    "origin": "calendar:google",
    "calendar_ref": {
        "provider": "google",
        "event_id": "evt-1",
        "series_id": "ser-1",
        "instance_start": "2026-10-26T09:00:00+03:00",
    },
}


def meeting(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "action": "log_time",
        "item": {"title": "Sprint planning"},
        "project": "WEB",
        "minutes": 60,
        "note": "Sprint planning with @sara",
        "source": CAL,
    }
    return base | over


async def entries(sf: async_sessionmaker[AsyncSession], *, live: bool = True) -> list[m.TimeEntry]:
    async with sf() as s:
        stmt = select(m.TimeEntry).order_by(m.TimeEntry.created_at)
        if live:
            stmt = stmt.where(m.TimeEntry.deleted_at.is_(None))
        return list((await s.scalars(stmt)).all())


async def test_log_time_with_note_records_origin_and_calendar_key(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    res = await api.send(**meeting())
    assert res["status"] == "applied"
    (te,) = await entries(session_factory)
    assert te.minutes == 60 and te.note == "Sprint planning with @sara"
    assert (te.calendar_provider, te.calendar_event_id) == ("google", "evt-1")
    assert te.client_id == res["client_id"]


async def test_second_send_of_the_same_instance_is_deduplicated(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    first = await api.send(**meeting())
    second = await api.send(**meeting())  # a different client_id, e.g. sent from the other laptop
    assert first["status"] == "applied" and second["status"] == "deduplicated"
    assert second["item"]["key"] == first["item"]["key"]
    assert len(await entries(session_factory)) == 1


async def test_another_instance_of_a_series_and_another_person_are_not_duplicates(
    api: Api, session_factory
) -> None:  # type: ignore[no-untyped-def]
    await api.send(**meeting())
    next_week = {
        **CAL,
        "calendar_ref": {**CAL["calendar_ref"], "instance_start": "2026-11-02T09:00:00+03:00"},
    }
    assert (await api.send(**meeting(source=next_week)))["status"] == "applied"
    assert (await api.send(as_="@sara", **meeting()))[
        "status"
    ] == "applied"  # same event on another person's calendar
    assert len(await entries(session_factory)) == 3


async def test_manual_messages_without_a_calendar_ref_never_dedup(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    for _ in range(2):
        assert (await api.send(**meeting(source={"origin": "personal"})))["status"] == "applied"
    assert len(await entries(session_factory)) == 2


async def test_unlog_removes_the_entry_emits_an_event_and_frees_the_instance(
    api: Api, session_factory
) -> None:  # type: ignore[no-untyped-def]
    sent = await api.send(**meeting())
    undo = await api.send(action="update", corrects=sent["client_id"], source={"origin": "personal"})
    assert undo["status"] == "applied"
    assert await entries(session_factory) == [] and len(await entries(session_factory, live=False)) == 1
    async with session_factory() as s:
        ev = await s.scalar(select(m.Event).where(m.Event.event_type == "time.unlogged"))
        assert ev is not None and ev.payload["corrects"] == sent["client_id"]
        assert ev.payload["before"]["minutes"] == 60
    # the occurrence can be sent again (a deliberate re-send after a correction)
    again = await api.send(**meeting())
    assert again["status"] == "applied" and len(await entries(session_factory)) == 1


async def test_unlog_is_idempotent_by_its_own_client_id(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    sent = await api.send(**meeting())
    body = {
        "workspace": "acme",
        "messages": [
            {
                "actor": "@mahmoud",
                "at": "2026-10-23T10:05:00+03:00",
                "client_id": "undo-1",
                "action": "update",
                "corrects": sent["client_id"],
            }
        ],
    }
    r1 = await api.call("POST", "/capture/batches", json=body)
    r2 = await api.call("POST", "/capture/batches", json=body)
    assert r1.json()["results"][0]["status"] == "applied"
    assert r2.json()["results"][0]["status"] == "deduplicated"
    async with session_factory() as s:
        n = await s.scalar(
            select(func.count()).select_from(m.Event).where(m.Event.event_type == "time.unlogged")
        )
    assert n == 1


async def test_only_the_owner_can_unlog(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    sent = await api.send(**meeting())
    other = await api.send(as_="@sara", action="update", corrects=sent["client_id"])
    assert other["status"] == "applied_with_warnings" and "unlog_target_not_found" in other["warnings"]
    assert len(await entries(session_factory)) == 1  # untouched


async def test_unlog_of_an_unknown_client_id_is_a_warning_not_an_error(api: Api) -> None:
    res = await api.send(action="update", corrects="nope")
    assert res["status"] == "applied_with_warnings" and res["warnings"] == ["unlog_target_not_found"]


# ---------------------------------------------------------------- development project (ADR-0043)


async def _setup_people(api: Api) -> None:
    r = await api.call(
        "POST", "/members", json={"display_name": "Omar", "email": "omar@acme.test", "handle": "@omar"}
    )
    assert r.status_code == 201, r.text
    line = {"person_id": await pid(api, "@sara"), "manager_id": await pid(api, "@ali"), "kind": "line"}
    assert (await api.call("POST", "/reporting-lines", json=line)).status_code == 201


async def test_development_project_is_created_on_first_use_private_and_tagged(
    api: Api, session_factory
) -> None:  # type: ignore[no-untyped-def]
    await _setup_people(api)
    res = await api.send(
        as_="@sara",
        action="log_time",
        item={"title": "Rust ch.3"},
        project="DEV",
        minutes=60,
        tags=["development"],
        source={"origin": "personal"},
    )
    assert res["status"] == "applied" and res["item"]["project_key"] == "DEV"
    assert res["resolution"]["project"]["method"] == "development"
    async with session_factory() as s:
        proj = await s.scalar(select(m.Project).where(m.Project.key == "DEV"))
        assert (
            proj is not None and proj.visibility.value == "private" and proj.settings == {"development": True}
        )
        item = await s.scalar(select(m.WorkItem).where(m.WorkItem.key == res["item"]["key"]))
        assert item is not None and item.visibility.value == "private"
        tags = (
            await s.scalars(
                select(m.Tag.name)
                .join(m.TagLink, m.TagLink.tag_id == m.Tag.id)
                .where(m.TagLink.target_type == TargetType.WORK_ITEM, m.TagLink.target_id == item.id)
            )
        ).all()
        assert list(tags) == ["development"]
        assert (
            await s.scalar(select(func.count()).select_from(m.ReviewItem)) == 0
        )  # the tag is not provisional
    # a second send reuses the project (no 409 on the key) and the same item
    again = await api.send(
        as_="@sara", action="log_time", item={"title": "Rust ch.3"}, project="DEV", minutes=30
    )
    assert again["item"]["key"] == res["item"]["key"]


async def test_development_items_are_visible_to_the_person_and_their_line_manager_only(api: Api) -> None:
    await _setup_people(api)
    res = await api.send(
        as_="@sara", action="log_time", item={"title": "Arabic course"}, project="DEV", minutes=60
    )
    key = res["item"]["key"]

    async def can_see(handle: str) -> bool:
        r = await api.call("GET", f"/items/{key}", as_=handle)
        listed = await api.call("GET", "/items", as_=handle, params={"project": "DEV"})
        in_list = (
            key in {i["key"] for i in listed.json().get("data", [])} if listed.status_code == 200 else False
        )
        assert (r.status_code == 200) == in_list
        return r.status_code == 200

    assert await can_see("@sara")  # the person
    assert await can_see("@ali")  # her line manager
    assert not await can_see("@omar")  # a colleague
    r = await api.call("GET", f"/items/{key}", as_="@omar")
    assert r.status_code == 404  # invisible, not forbidden (ADR-0008)
    # a workspace admin (@mahmoud) follows the platform rule for private items, which ADR-0043 records as a known limit
    assert await can_see("@mahmoud")


async def test_the_development_project_key_is_a_workspace_setting(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws is not None
        ws.settings = {**ws.settings, "development": {"project_key": "learn"}}
        await s.commit()
    res = await api.send(
        as_="@sara", action="log_time", item={"title": "Book club"}, project="LEARN", minutes=20
    )
    assert res["item"]["project_key"] == "LEARN" and res["resolution"]["project"]["method"] == "development"
    pack = (await api.call("GET", "/context-pack", as_="@sara")).json()
    assert pack["development_project"] == {"key": "LEARN", "tag": "development", "exists": True}


# ---------------------------------------------------------------- ContextPack


async def test_context_pack_carries_development_calendar_hints_and_capacity(
    api: Api, session_factory
) -> None:  # type: ignore[no-untyped-def]
    pack = (await api.call("GET", "/context-pack", as_="@sara")).json()
    assert pack["development_project"] == {"key": "DEV", "tag": "development", "exists": False}
    assert pack["my_capacity_hours_per_week"] is None
    assert pack["calendar_mapping_hints"] == {"teams": [], "client_domains": []}
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws is not None
        ws.settings = {**ws.settings, "calendar": {"client_domains": {"Acme.com": "WEB"}}}
        mem = await s.scalar(
            select(m.WorkspaceMember).where(
                m.WorkspaceMember.workspace_id == ws.id, m.WorkspaceMember.handle == "@sara"
            )
        )
        assert mem is not None
        mem.capacity_hours_per_week = 32
        await s.commit()
    pack = (await api.call("GET", "/context-pack", as_="@sara")).json()
    assert pack["calendar_mapping_hints"]["client_domains"] == [{"domain": "acme.com", "project": "WEB"}]
    assert pack["my_capacity_hours_per_week"] == 32.0


async def test_capture_still_accepts_a_pre_phase_15_message(api: Api) -> None:
    body = {
        "workspace": "acme",
        "client_version": "0.1",
        "messages": [
            {
                "actor": "@mahmoud",
                "at": "2026-10-23T10:00:00+03:00",
                "client_id": str(uuid.uuid4()),
                "action": "log_time",
                "item": {"title": "legacy"},
                "minutes": 5,
            }
        ],
    }
    r = await api.call("POST", "/capture/batches", json=body)
    assert r.status_code == 200 and r.json()["results"][0]["status"] in ("applied", "applied_with_warnings")
