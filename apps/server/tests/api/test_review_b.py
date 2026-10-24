"""Review 2026-10-24, package B: access-control completeness (findings 8-23)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id
from tt_schema.enums import HabitStatus, PolicyCategory, Polarity, TargetType, Trend
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.integrations import idp

from tests.api.conftest import Api
from tests.api.test_acl_governance import active_item, make_policy


async def _share(
    api: Api, session_factory: async_sessionmaker[AsyncSession], level: str | None = None
) -> str:
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws
        ws.settings = {
            **ws.settings,
            "external_share": True,
            **({"external_share_redaction": level} if level else {}),
        }
        await s.commit()
    p = (
        await api.call(
            "POST",
            "/presentations",
            json={"title": "D", "slides": [{"kind": "violations"}, {"kind": "kpis"}]},
        )
    ).json()
    return str((await api.call("POST", f"/presentations/{p['id']}/share", json={})).json()["token"])


async def test_share_links_never_carry_wellbeing_habits_or_live_sessions(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    people = {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws
        s.add(
            m.Habit(
                id=new_id(), workspace_id=ws.id, subject_type=TargetType.PERSON, subject_id=people["@mahmoud"], key="late_nights",
                polarity=Polarity.NEGATIVE, category=PolicyCategory.WELLBEING, trend=Trend.STABLE, status=HabitStatus.OBSERVED,
                occurrences=3, rate=Decimal("0.5"), score=Decimal("1"), window="30d",
            )
        )  # fmt: skip
        await s.commit()
    owner_view = (await api.call("GET", "/snapshot", params={"fresh": 1})).json()
    assert owner_view["habits"], "the owner sees their own habits"
    token = await _share(api, session_factory)
    snap = (await api.c.get(f"/api/v1/public/p/{token}/data")).json()["snapshot"]
    assert snap["habits"] == [] and snap["now"] == []


async def test_strict_sharing_pseudonymises_people(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await active_item(api, "someone's work", assignee="@sara")
    token = await _share(api, session_factory, level="strict")
    body = (await api.c.get(f"/api/v1/public/p/{token}/data")).text
    assert "@sara" not in body and "@mahmoud" not in body and "Person 1" in body


async def test_insight_slides_follow_project_visibility(api: Api) -> None:
    from tt_schema.enums import InsightKind, ScopeType

    p = await api.call(
        "POST",
        "/presentations",
        as_="@ali",
        json={"title": "I", "slides": [{"kind": "insight", "config": {"scope": "WEB"}}]},
    )
    assert p.status_code == 201, p.text
    deck = p.json()
    gen = await api.call("POST", "/insights:generate", json={"scope": "WEB"})
    assert gen.status_code == 201
    data = (await api.call("GET", f"/presentations/{deck['id']}/data", as_="@ali")).json()["slide_data"]
    ins = data[deck["slides"][0]["id"]]
    assert ins["insights"] == []  # @ali cannot see WEB, so its insights are not his
    mine = await api.call(
        "POST",
        "/presentations",
        json={"title": "J", "slides": [{"kind": "insight", "config": {"scope": "WEB"}}]},
    )
    d2 = mine.json()
    seen = (await api.call("GET", f"/presentations/{d2['id']}/data")).json()["slide_data"][
        d2["slides"][0]["id"]
    ]
    assert seen["insights"]
    _ = (InsightKind, ScopeType)


async def test_metric_snapshots_are_manager_only_for_aggregates(api: Api) -> None:
    ws_scope = {"scope_type": "workspace", "scope_id": "acme"}
    assert (await api.call("GET", "/metric-snapshots", as_="@ali", params=ws_scope)).status_code == 403
    assert (await api.call("GET", "/metric-snapshots", params=ws_scope)).status_code == 200
    assert (
        await api.call("GET", "/metric-snapshots", as_="@ali", params={"scope_type": "team", "scope_id": "x"})
    ).status_code == 403


async def test_capture_cannot_reach_an_item_the_reporter_cannot_see(api: Api) -> None:
    secret = await api.send(as_="@sara", action="create", item={"title": "sara's private inbox thing"})
    key = secret["item"]["key"]
    probe = await api.send(as_="@ali", action="log_time", item={"key": key}, minutes=5)
    assert "sara's private" not in str(probe) and "item_not_found" in (probe.get("warnings") or [])
    own = await api.send(as_="@sara", action="log_time", item={"key": key}, minutes=5)
    assert own["item"]["key"] == key


async def test_one_provider_sync_never_removes_another_providers_roles(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    people = {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        role = await s.scalar(select(m.Role).where(m.Role.key == "manager"))
        assert ws and role
        s.add(
            m.RoleAssignment(
                id=new_id(), workspace_id=ws.id, person_id=people["@sara"], role_id=role.id,
                scope_type=__import__("tt_schema.enums", fromlist=["ScopeType"]).ScopeType.WORKSPACE, scope_id=ws.id, source="idp:oidc",
            )
        )  # fmt: skip
        await s.commit()
    uow = UnitOfWork(session_factory)
    async with uow:
        ws = await uow.session.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws
        delta = await idp.apply_groups(uow, ws, people["@sara"], "ldap", frozenset())  # LDAP says "no groups"
        assert delta.roles_removed == []
        await uow.commit()
    async with session_factory() as s:
        row = await s.scalar(
            select(m.RoleAssignment).where(
                m.RoleAssignment.source == "idp:oidc", m.RoleAssignment.person_id == people["@sara"]
            )
        )
        assert row is not None and row.deleted_at is None


async def test_role_notifications_stay_inside_their_workspace(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    r = await api.c.post(
        "/api/v1/workspaces",
        json={
            "name": "Beta",
            "slug": "beta",
            "owner": {"display_name": "Bea", "email": "bea@beta.test", "handle": "@bea"},
        },
    )
    bea = r.json()["owner"]["person_id"]
    beta = Api(api.c, "beta")
    await beta.role("@bea", "manager", as_="@bea") if False else None
    await active_item(api, "no estimate here")
    await make_policy(api, "notify_roles", notify={"roles": ["manager", "workspace_admin"]})
    await api.call("POST", "/policies/notify_roles:run", json={})
    async with session_factory() as s:
        got = set((await s.scalars(select(m.Notification.person_id))).all())
    assert bea not in got and got, "mahmoud (admin of acme) is notified, bea (admin of beta) is not"
    _ = beta
