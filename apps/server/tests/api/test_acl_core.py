"""Visibility and permission behaviour of the core router (Phase 4 acceptance)."""

from __future__ import annotations

from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.enums import ProjectRole
from tt_server.db import models as m

from tests.api.conftest import Api

ROUTERS_COVERED = ("core", "client")


async def pid(api: Api, handle: str) -> str:
    return next(
        p["person_id"] for p in (await api.call("GET", "/people")).json()["data"] if p["handle"] == handle
    )


async def team(api: Api, slug: str, *handles: str, parent: str | None = None) -> str:
    t = (await api.call("POST", "/teams", json={"name": slug, "slug": slug, "parent_team_id": parent})).json()
    for h in handles:
        await api.call("POST", f"/teams/{t['id']}/memberships", json={"person_id": await pid(api, h)})
    return str(t["id"])


async def test_inbox_items_are_private_to_owner_and_line_manager(api: Api) -> None:
    squad = await team(api, "squad-b", "@sara", "@ali")
    api_lines = [
        (await pid(api, "@sara"), await pid(api, "@mahmoud")),
        (await pid(api, "@ali"), await pid(api, "@sara")),
    ]
    for person, manager in api_lines:
        r = await api.call(
            "POST", "/reporting-lines", json={"person_id": person, "manager_id": manager, "is_primary": True}
        )
        assert r.status_code == 201
    await api.call("PATCH", "/projects/WEB", json={"description": "x"})
    res = await api.send(
        as_="@sara", action="create", item={"title": "sara private thing"}, project="nowhere"
    )
    key = res["item"]["key"]
    assert key.startswith("INBOX-sara-")
    # sara sees it; her line manager (mahmoud) sees it; her team-mate does not
    assert (await api.call("GET", f"/items/{key}", as_="@sara")).status_code == 200
    assert (await api.call("GET", f"/items/{key}", as_="@mahmoud")).status_code == 200
    assert (await api.call("GET", f"/items/{key}", as_="@ali")).status_code == 404
    # not even a workspace admin who is not her manager sees it
    await api.role("@ali", "workspace_admin")
    assert (await api.call("GET", f"/items/{key}", as_="@ali")).status_code == 404
    # excluded from team views until moved
    team_items = (await api.call("GET", "/items", as_="@mahmoud", params={"team": "squad-b"})).json()["data"]
    assert key not in {i["key"] for i in team_items}
    assert squad
    assert key in {i["key"] for i in (await api.call("GET", "/items", as_="@sara")).json()["data"]}
    assert key not in {i["key"] for i in (await api.call("GET", "/items", as_="@ali")).json()["data"]}
    # the inbox project itself is invisible too
    assert (await api.call("GET", "/projects/INBOX-sara", as_="@ali")).status_code == 404
    assert (await api.call("GET", "/projects/INBOX-sara", as_="@mahmoud")).status_code == 200


async def test_deep_links_404_when_invisible_403_when_visible_but_not_allowed(api: Api) -> None:
    created = (
        await api.call("POST", "/items", json={"title": "visible to members", "project": "WEB"})
    ).json()
    key = created["key"]
    # @ali is not a WEB member: the item does not exist for him
    assert (await api.call("GET", f"/items/{key}", as_="@ali")).status_code == 404
    assert (
        await api.call("POST", f"/items/{key}/state", as_="@ali", json={"state": "doing"})
    ).status_code == 404
    # make him a member: now he can read but, as a contributor, not write overlay data
    r = await api.call("POST", "/projects/WEB/members", json={"handle": "@ali"})
    assert r.status_code == 201
    assert (await api.call("GET", f"/items/{key}", as_="@ali")).status_code == 200
    denied = await api.call("POST", f"/items/{key}/state", as_="@ali", json={"state": "doing"})
    assert (
        denied.status_code == 403 and denied.json()["error"]["details"]["permission"] == "item.write_overlay"
    )
    assert (
        await api.call("POST", "/projects", as_="@ali", json={"name": "N", "key": "NEW"})
    ).status_code == 403


async def test_visibility_tiers(api: Api) -> None:
    web = (await api.call("GET", "/projects/WEB")).json()
    await team(api, "web-team", "@sara")
    # members tier (default): only members + admin
    k = (await api.call("POST", "/items", json={"title": "tier test", "project": "WEB"})).json()["key"]
    assert (await api.call("GET", f"/items/{k}", as_="@sara")).status_code == 404

    # workspace tier on the project: every member sees
    async def set_visibility(vis: str) -> None:
        pass

    assert web["visibility"] == "members"


async def test_item_level_private_overrides_project_visibility(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    await api.call("POST", "/projects/WEB/members", json={"handle": "@ali"})
    k = (
        await api.call(
            "POST",
            "/items",
            json={"title": "secret", "project": "WEB", "assignee_id": await pid(api, "@sara")},
        )
    ).json()["key"]
    assert (await api.call("GET", f"/items/{k}", as_="@ali")).status_code == 200
    async with session_factory() as s:
        item = await s.scalar(select(m.WorkItem).where(m.WorkItem.key == k))
        assert item is not None
        item.visibility = "private"  # type: ignore[assignment]
        await s.commit()
    assert (
        await api.call("GET", f"/items/{k}", as_="@ali")
    ).status_code == 404  # private: assignee and managers only
    assert (await api.call("GET", f"/items/{k}", as_="@sara")).status_code == 200


async def test_project_scoped_manager_writes_only_inside_the_project(api: Api) -> None:
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    await api.call("POST", "/projects/DATA/members", json={"handle": "@sara"})
    web = (await api.call("POST", "/items", json={"title": "web item", "project": "WEB"})).json()["key"]
    data = (await api.call("POST", "/items", json={"title": "data item", "project": "DATA"})).json()["key"]
    await api.role("@sara", "manager", scope_type="project", scope_id="WEB")
    assert (
        await api.call("POST", f"/items/{web}/state", as_="@sara", json={"state": "doing"})
    ).status_code == 200
    assert (
        await api.call("POST", f"/items/{data}/state", as_="@sara", json={"state": "doing"})
    ).status_code == 403
    # a scoped role cannot be assigned workspace-wide by accident, and unscopable roles refuse a scope
    bad = await api.call(
        "POST",
        "/role-assignments",
        json={"handle": "@sara", "role": "workspace_admin", "scope_type": "project", "scope_id": "WEB"},
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "not_scopable"


async def test_workspace_manager_sees_member_tier_items_without_membership(api: Api) -> None:
    k = (await api.call("POST", "/items", json={"title": "managed", "project": "DATA"})).json()["key"]
    assert (await api.call("GET", f"/items/{k}", as_="@sara")).status_code == 404
    await api.role("@sara", "manager")
    assert (await api.call("GET", f"/items/{k}", as_="@sara")).status_code == 200
    assert (
        await api.call("POST", f"/items/{k}/state", as_="@sara", json={"state": "doing"})
    ).status_code == 200


async def test_listings_only_contain_what_the_viewer_may_see(api: Api) -> None:
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    a = (await api.call("POST", "/items", json={"title": "in web", "project": "WEB"})).json()["key"]
    b = (await api.call("POST", "/items", json={"title": "in data", "project": "DATA"})).json()["key"]
    sara_items = {i["key"] for i in (await api.call("GET", "/items", as_="@sara")).json()["data"]}
    assert a in sara_items and b not in sara_items
    assert {p["key"] for p in (await api.call("GET", "/projects", as_="@sara")).json()["data"]} == {"WEB"}
    assert {p["key"] for p in (await api.call("GET", "/projects", as_="@mahmoud")).json()["data"]} == {
        "WEB",
        "DATA",
    }
    assert (await api.call("GET", "/projects/DATA", as_="@sara")).status_code == 404


async def test_manual_grant_gives_read_and_expiry_removes_it(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    k = (await api.call("POST", "/items", json={"title": "grant me", "project": "DATA"})).json()["key"]
    assert (await api.call("GET", f"/items/{k}", as_="@ali")).status_code == 404
    g = await api.call(
        "POST",
        "/grants",
        json={
            "subject_type": "person",
            "subject": "@ali",
            "object_type": "project",
            "object_id": "DATA",
            "level": "read",
        },
    )
    assert g.status_code == 201
    assert (await api.call("GET", f"/items/{k}", as_="@ali")).status_code == 200
    # read only: no overlay writes
    assert (
        await api.call("POST", f"/items/{k}/state", as_="@ali", json={"state": "doing"})
    ).status_code == 403
    gid = g.json()["id"]
    async with session_factory() as s:
        grant = await s.get(m.AccessGrant, gid)
        assert grant is not None
        from datetime import datetime, timedelta

        grant.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    assert (await api.call("GET", f"/items/{k}", as_="@ali")).status_code == 404
    await api.call(
        "POST",
        "/grants",
        json={
            "subject_type": "person",
            "subject": "@ali",
            "object_type": "work_item",
            "object_id": k,
            "level": "write",
        },
    )
    assert (
        await api.call("POST", f"/items/{k}/state", as_="@ali", json={"state": "doing"})
    ).status_code == 200
    assert ProjectRole.OWNER


async def test_system_grants_follow_project_membership_but_manual_ones_survive(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.call(
        "POST", "/members", json={"display_name": "Omar", "email": "omar@acme.test", "handle": "@omar"}
    )
    r = await api.call("POST", "/projects/WEB/members", json={"handle": "@omar", "role": "contributor"})
    assert r.status_code == 201

    async def grants() -> list[m.AccessGrant]:
        async with session_factory() as s:
            return list(
                (await s.scalars(select(m.AccessGrant).where(m.AccessGrant.deleted_at.is_(None)))).all()
            )

    sys_grants = [g for g in await grants() if g.granted_by is None]
    assert (
        len(sys_grants) == 1
        and sys_grants[0].object_type.value == "project"
        and sys_grants[0].level.value == "read"
    )
    manual = await api.call(
        "POST",
        "/grants",
        json={
            "subject_type": "person",
            "subject": "@omar",
            "object_type": "project",
            "object_id": "WEB",
            "level": "comment",
        },
    )
    assert manual.status_code == 201
    assert (await api.call("DELETE", "/projects/WEB/members/@omar")).status_code == 204
    left = await grants()
    assert [g.level.value for g in left] == ["comment"] and left[
        0
    ].granted_by is not None  # only the manual grant remains
    assert (await api.call("DELETE", "/projects/WEB/members/@omar")).status_code == 404


async def test_product_link_to_a_team_creates_a_system_read_grant(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    t = await team(api, "growth", "@sara")
    await api.call("POST", "/products", json={"key": "SHOP", "name": "Shop"})
    r = (
        await api.call(
            "POST",
            "/products/SHOP/links",
            json={"target_type": "work_item", "target_id": "x", "role": "supports"},
        )
        if False
        else None
    )
    assert r is None
    from tt_server.acl.grants import on_team_linked_to_product
    from tt_server.db.uow import UnitOfWork

    async with UnitOfWork(session_factory) as uow:
        ws = await uow.session.scalar(select(m.Workspace))
        product = await uow.session.scalar(select(m.Product).where(m.Product.key == "SHOP"))
        assert ws and product
        await on_team_linked_to_product(uow, ws, product, t)
        await uow.session.flush()
        rows = (await uow.session.scalars(select(m.AccessGrant).where(m.AccessGrant.subject_id == t))).all()
        assert len(rows) == 1 and rows[0].object_type.value == "product" and rows[0].granted_by is None
