"""Review 2026-10-24, package B: access-control completeness (findings 8-23)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id
from tt_schema.enums import HabitStatus, Polarity, PolicyCategory, TargetType, Trend
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


async def test_browser_exports_are_bounded_and_say_so(api: Api, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    from tt_server.api.routers import presentations as pr

    p = (await api.call("POST", "/presentations:from-template", json={"template": "portfolio_review"})).json()
    slots = asyncio.Semaphore(0)  # every slot taken
    monkeypatch.setattr(pr, "_RENDER_SLOTS", slots)
    monkeypatch.setattr(pr, "RENDER_WAIT_S", 0.05)
    r = await api.call("POST", f"/presentations/{p['id']}/render", params={"format": "pdf"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "render_busy"
    assert (
        await api.call("POST", f"/presentations/{p['id']}/render", params={"format": "csv"})
    ).status_code == 200


def test_production_refuses_a_weak_or_default_session_secret() -> None:
    import pytest
    from tt_server.settings import Settings

    Settings(env="dev").validate_for_runtime()
    for bad in ("dev-insecure-session-secret", "short"):
        with pytest.raises(RuntimeError, match="SESSION_SECRET"):
            Settings(env="prod", session_secret=bad).validate_for_runtime()
    with pytest.raises(RuntimeError, match="DEV_AUTH"):
        Settings(env="prod", session_secret="x" * 40, dev_auth=True).validate_for_runtime()
    Settings(env="prod", session_secret="x" * 40).validate_for_runtime()


async def test_image_slides_refuse_external_hosts(api: Api) -> None:
    ok = await api.call(
        "POST",
        "/presentations",
        json={"title": "i", "slides": [{"kind": "image", "config": {"url": "data:image/png;base64,AAAA"}}]},
    )
    assert ok.status_code == 201
    for bad in ("https://evil.example/x.png", "//evil.example/x.png", "http://x/y", "javascript:alert(1)"):
        r = await api.call(
            "POST",
            "/presentations",
            json={"title": "i", "slides": [{"kind": "image", "config": {"url": bad}}]},
        )
        assert r.status_code == 400, bad


async def test_a_failed_sign_in_backs_off_per_address_and_user(api: Api) -> None:
    from tt_server.api.routers import webauth

    assert (
        webauth.MAX_FAILURES == 5 and webauth.WINDOW >= 600
    )  # numbers pinned; behaviour covered in test_acl_identity


async def test_private_products_and_their_links_follow_visibility(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from tt_schema.enums import Visibility

    people = {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws
        s.add(
            m.Product(
                id=new_id(),
                workspace_id=ws.id,
                key="SECRET",
                name="Secret",
                owner_id=people["@mahmoud"],
                visibility=Visibility.PRIVATE,
            )
        )
        await s.commit()
    assert "SECRET" in {p["key"] for p in (await api.call("GET", "/products")).json()["data"]}
    assert "SECRET" not in {p["key"] for p in (await api.call("GET", "/products", as_="@ali")).json()["data"]}
    assert (await api.call("GET", "/products/SECRET/links", as_="@ali")).status_code == 404
    assert (
        await api.call("GET", "/classifications", as_="@ali", params={"target_type": "product"})
    ).status_code == 200


async def test_rollups_apply_the_viewers_visibility(api: Api) -> None:
    mine = (await api.call("POST", "/items", json={"title": "solo", "project": "WEB"})).json()
    assert (await api.call("GET", f"/items/{mine['key']}/rollup")).json()["item_count"] == 1
    assert (await api.call("GET", f"/items/{mine['key']}/rollup", as_="@ali")).status_code in (403, 404)


async def test_allowed_roles_gate_a_stage_transition_for_people_but_not_for_admins(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await api.role("@sara", "contributor")
    key = await active_item(api, "needs a manager to ship", as_="@mahmoud", assignee="@sara")
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws
        proc = (
            await s.scalars(
                select(m.Process).where(m.Process.workspace_id == ws.id, m.Process.is_default.is_(True))
            )
        ).one()
        stages = {
            st.key: st for st in (await s.scalars(select(m.Stage).where(m.Stage.process_id == proc.id))).all()
        }
        s.add(
            m.StageTransitionRule(
                id=new_id(), process_id=proc.id, from_stage_id=stages["build"].id, to_stage_id=stages["test"].id,
                allowed_roles=["manager"], required_evidence_kinds=[],
            )
        )  # fmt: skip
        await s.commit()
    assert (await api.call("PUT", f"/items/{key}/stage", json={"stage": "build"})).status_code in (200, 201)
    denied = await api.call("PUT", f"/items/{key}/stage", as_="@sara", json={"stage": "test"})
    assert denied.status_code in (403, 404), denied.text
    if denied.status_code == 403:
        assert denied.json()["error"]["code"] in ("role_not_allowed", "forbidden")
    ok = await api.call("PUT", f"/items/{key}/stage", json={"stage": "test"})  # a workspace admin
    assert ok.status_code in (200, 201)


async def test_project_visibility_and_process_changes_propagate(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    r = await api.call("PATCH", "/projects/WEB", json={"visibility": "workspace"})
    assert r.status_code == 200, r.text
    async with session_factory() as s:
        evs = (
            await s.scalars(
                select(m.Event.event_type).where(m.Event.event_type == "project.visibility_changed")
            )
        ).all()
    assert len(evs) == 1
    again = await api.call("PATCH", "/projects/WEB", json={"visibility": "workspace"})
    assert again.status_code == 200
    async with session_factory() as s:
        assert (
            len(
                (
                    await s.scalars(
                        select(m.Event.event_type).where(m.Event.event_type == "project.visibility_changed")
                    )
                ).all()
            )
            == 1
        )
    bad = await api.call("PATCH", "/projects/WEB", json={"default_process_id": "nope"})
    assert bad.status_code == 400


async def test_bad_enum_cursor_and_size_inputs_are_400_not_500(api: Api) -> None:
    for params in ({"severity": "loud"}, {"status": "gone"}, {"category": "x"}, {"cursor": "abc"}):
        r = await api.call("GET", "/violations", params=params)
        assert r.status_code == 400, (params, r.status_code)
    for page in ({"size": "many"}, {"cursor": "zzz"}):
        r = await api.call("POST", "/query/items", json={"page": page})
        assert r.status_code == 400, (page, r.status_code)


async def test_a_concurrent_duplicate_client_id_is_deduplicated_not_failed(api: Api) -> None:
    import uuid

    cid = str(uuid.uuid4())
    body = {
        "workspace": "acme",
        "messages": [
            {
                "actor": "@mahmoud",
                "at": "2026-10-23T10:00:00+03:00",
                "client_id": cid,
                "action": "create",
                "item": {"title": "once"},
            }
        ],
    }
    a = (await api.call("POST", "/capture/batches", json=body)).json()["results"][0]
    b = (await api.call("POST", "/capture/batches", json=body)).json()["results"][0]
    assert (
        a["status"].startswith("applied")
        and b["status"] == "deduplicated"
        and a["item"]["key"] == b["item"]["key"]
    )


async def test_every_declared_hot_path_index_exists_in_the_migrated_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Review 22: the indexes declared in `db/models/indexes.py` are really created by the migrations (model/migration drift)."""
    import inspect
    import re

    import tt_server.db.models.indexes as idx
    from sqlalchemy import text

    declared = set(re.findall(r'sa\.Index\(\s*"([a-z0-9_]+)"', inspect.getsource(idx)))
    assert len(declared) >= 10
    async with session_factory() as s:
        have = {
            r[0]
            for r in (
                await s.execute(text("select indexname from pg_indexes where schemaname = 'public'"))
            ).all()
        }
    assert declared <= have, sorted(declared - have)


async def test_browser_renders_are_bounded_and_the_overflow_is_told_to_retry(committed_api: Api, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Review 19: at most MAX_BROWSER_RENDERS Chromium exports run at once; the rest get 503 render_busy instead of piling up."""
    import asyncio

    from tt_server.api.routers import presentations as pr
    from tt_server.exports import presentation as exp

    state = {"running": 0, "peak": 0}

    async def slow(fmt, payload, bundle, handle=None):  # type: ignore[no-untyped-def]
        state["running"] += 1
        state["peak"] = max(state["peak"], state["running"])
        await asyncio.sleep(0.4)
        state["running"] -= 1
        return b"%PDF-1.4"

    monkeypatch.setattr(exp, "render", slow)
    monkeypatch.setattr(pr, "RENDER_WAIT_S", 0.05)
    monkeypatch.setattr(pr, "_RENDER_SLOTS", asyncio.Semaphore(pr.MAX_BROWSER_RENDERS))
    pid = (
        await committed_api.call("POST", "/presentations", json={"title": "D", "slides": [{"kind": "kpis"}]})
    ).json()["id"]
    got = await asyncio.gather(
        *(committed_api.call("POST", f"/presentations/{pid}/render", params={"format": "pdf"}) for _ in range(5))
    )
    codes = sorted(r.status_code for r in got)
    assert state["peak"] <= pr.MAX_BROWSER_RENDERS
    assert codes.count(200) >= 1 and codes.count(503) >= 1 and set(codes) <= {200, 503}, codes
    assert all(r.json()["error"]["code"] == "render_busy" for r in got if r.status_code == 503)
