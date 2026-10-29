"""Server gaps found by the web pass: product link removal, same-project item moves, the external_packs switch."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m

from tests.api.conftest import Api

ROUTERS_COVERED = ("overlay_products", "console", "core")


async def _event_types(api: Api, event_type: str) -> list[dict[str, object]]:
    r = await api.call("GET", "/events", params={"event_type": event_type, "limit": 50})
    assert r.status_code == 200, r.text
    return r.json()["data"]  # type: ignore[no-any-return]


async def _types(sf: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    async with sf() as s:
        rows = (await s.execute(select(m.WorkItemType.key, m.WorkItemType.id))).all()
    return {str(getattr(k, "value", k)): i for k, i in rows}


async def _live_inherited(sf: async_sessionmaker[AsyncSession], product_key: str) -> int:
    async with sf() as s:
        pid = await s.scalar(select(m.Product.id).where(m.Product.key == product_key))
        rows = (
            await s.scalars(
                select(m.Classification).where(
                    m.Classification.inherited_from_id == pid, m.Classification.deleted_at.is_(None)
                )
            )
        ).all()
        return len(rows)


# ---------------------------------------------------------------- (1) DELETE /products/{ref}/links/{project}
async def test_a_project_is_unlinked_from_a_product_and_relinked_under_another(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    for key in ("SHOP", "OPS"):
        assert (await api.call("POST", "/products", json={"key": key, "name": key})).status_code == 201
    await api.call("POST", "/items", json={"title": "one", "project": "WEB"})
    link = {"target_type": "project", "target_id": "WEB", "role": "delivers"}
    assert (await api.call("POST", "/products/SHOP/links", json=link)).status_code == 201
    assert await _live_inherited(session_factory, "SHOP") >= 2  # the project and its item

    r = await api.call("DELETE", "/products/SHOP/links/WEB")
    assert r.status_code == 204, r.text
    assert (await api.call("GET", "/products/SHOP/links")).json()["data"] == []
    assert await _live_inherited(session_factory, "SHOP") == 0  # inherited product values go with the link
    removed = await _event_types(api, "product_link.removed")
    assert len(removed) == 1 and removed[0]["entity_id"]

    # the delivering slot is free again: the move is unlink + link
    assert (await api.call("POST", "/products/OPS/links", json=link)).status_code == 201
    assert (await api.call("DELETE", "/products/SHOP/links/WEB")).status_code == 404  # already gone


async def test_unlinking_needs_product_admin_and_an_existing_link(api: Api) -> None:
    await api.call("POST", "/products", json={"key": "SHOP", "name": "Shop"})
    await api.call(
        "POST",
        "/products/SHOP/links",
        json={"target_type": "project", "target_id": "WEB", "role": "delivers"},
    )
    assert (await api.call("DELETE", "/products/SHOP/links/WEB", as_="@ali")).status_code == 403
    assert (await api.call("DELETE", "/products/NOPE/links/WEB")).status_code == 404
    assert (await api.call("DELETE", "/products/SHOP/links/NOPE")).status_code == 404
    assert len((await api.call("GET", "/products/SHOP/links")).json()["data"]) == 1


# ---------------------------------------------------------------- (1b) item moves stay inside the project
async def test_an_item_cannot_be_moved_under_a_parent_of_another_project(api: Api) -> None:
    web = (await api.call("POST", "/items", json={"title": "w", "project": "WEB"})).json()
    other = await api.call("POST", "/projects", json={"key": "OPS2", "name": "Ops two"})
    assert other.status_code == 201, other.text
    parent = (await api.call("POST", "/items", json={"title": "p", "project": "OPS2"})).json()
    r = await api.call("POST", f"/items/{web['key']}/move", json={"parent": parent["key"]})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "cross_project_move"
    again = (await api.call("GET", f"/items/{web['key']}")).json()
    assert again["parent_id"] is None and again["project_id"] == web["project_id"]


async def test_a_same_project_move_still_works_and_can_go_back_to_the_top(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    types = await _types(session_factory)
    epic = (
        await api.call("POST", "/items", json={"title": "e", "project": "WEB", "type_id": types["epic"]})
    ).json()
    task = (
        await api.call("POST", "/items", json={"title": "t", "project": "WEB", "type_id": types["task"]})
    ).json()
    assert (
        await api.call("POST", f"/items/{task['key']}/move", json={"parent": epic["key"]})
    ).status_code == 200
    r = await api.call("POST", f"/items/{task['key']}/move", json={"parent": None})
    assert r.status_code == 200 and r.json()["parent_id"] is None


# ---------------------------------------------------------------- (2) external_packs setting
async def test_external_packs_is_a_workspace_setting_admins_switch(api: Api) -> None:
    assert (await api.call("GET", "/settings")).json()["external_packs"] is False
    r = await api.call("POST", "/presentations:generation-pack", json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "external_packs_disabled"

    assert (
        await api.call("PATCH", "/settings", as_="@ali", json={"external_packs": True})
    ).status_code == 403
    r = await api.call("PATCH", "/settings", json={"external_packs": True})
    assert r.status_code == 200 and r.json()["external_packs"] is True
    assert (await api.call("GET", "/settings")).json()["external_packs"] is True
    assert len(await _event_types(api, "workspace.settings_changed")) == 1

    ok = await api.call("POST", "/presentations:generation-pack", json={})
    assert ok.status_code == 200, ok.text

    await api.call("PATCH", "/settings", json={"external_packs": False})
    r = await api.call("POST", "/presentations:generation-pack", json={})
    assert r.status_code == 403


async def test_allow_same_level_nesting_is_a_workspace_setting_admins_switch(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    assert (await api.call("GET", "/settings")).json()["allow_same_level_nesting"] is False
    types = await _types(session_factory)
    a = (
        await api.call("POST", "/items", json={"title": "a", "project": "WEB", "type_id": types["epic"]})
    ).json()
    b = (
        await api.call("POST", "/items", json={"title": "b", "project": "WEB", "type_id": types["epic"]})
    ).json()
    r = await api.call("POST", f"/items/{b['key']}/move", json={"parent": a["key"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_nesting"
    assert (
        await api.call("PATCH", "/settings", as_="@ali", json={"allow_same_level_nesting": True})
    ).status_code == 403
    assert (await api.call("PATCH", "/settings", json={"allow_same_level_nesting": True})).status_code == 200
    r = await api.call("POST", f"/items/{b['key']}/move", json={"parent": a["key"]})
    assert r.status_code == 200 and r.json()["parent_id"] == a["id"]
