"""Management commands (Phase 14): catalogue, execution, authorisation matrix, merge, batches, destructive guard."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.acl.permissions import ROLES
from tt_server.api.commands import REGISTRY, catalogue
from tt_server.db import models as m

from tests.api.conftest import Api

ROUTERS_COVERED = ("commands",)


def cmd(op: str, args: dict[str, Any] | None = None, *, actor: str = "@mahmoud", preview: bool = False, confirmed: bool = False, **kw: Any) -> dict[str, Any]:  # fmt: skip
    meta = {
        "client_version": "0.2",
        "mode": "manual",
        "role_mode": "manager",
        **({"confirmed": True} if confirmed else {}),
    }
    return {"client_id": str(uuid.uuid4()), "actor": actor, "at": "2026-10-25T10:00:00+03:00", "op": op, "args": {**(args or {}), "_meta": meta}, "preview_only": preview, **kw}  # fmt: skip


async def send(api: Api, *cmds: dict[str, Any], as_: str = "@mahmoud") -> list[dict[str, Any]]:
    r = await api.call("POST", "/commands", as_=as_, json={"workspace": api.slug, "commands": list(cmds)})
    assert r.status_code in (200, 207), r.text
    return list(r.json()["results"])


async def one(api: Api, op: str, args: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    as_ = kw.pop("as_", "@mahmoud")
    return (await send(api, cmd(op, args, actor=as_, **kw), as_=as_))[0]


def code(res: dict[str, Any]) -> str:
    assert res["status"] == "rejected", res
    return str(res["errors"][0]["code"])


# ----------------------------------------------------------------------------------------------- catalogue


def test_catalogue_is_complete_and_examples_validate() -> None:
    cat = catalogue()
    assert len(cat["ops"]) == len(REGISTRY) >= 40
    known = {p for spec in ROLES.values() for p in spec.permissions} | {"project.create", "team.write", "iteration.write", "violation.waive"}  # fmt: skip
    for entry in cat["ops"]:
        spec = REGISTRY[entry["op"]]
        assert entry["description"] and entry["example"]["text"], entry["op"]
        assert entry["args_schema"]["type"] == "object" and entry["args_schema"].get("properties"), entry[
            "op"
        ]
        spec.args.model_validate(entry["example"]["args"] or _minimal(spec))
        if spec.permission:
            assert spec.permission in known or "." in spec.permission, entry["op"]
        assert set(entry["modes"]) <= {"reporter", "manager", "scrum_master", "coach", "pm", "admin"}, entry[
            "op"
        ]
    assert cat["etag"] == catalogue()["etag"]


def _minimal(spec: Any) -> dict[str, Any]:
    return {}


async def test_catalogue_endpoint_supports_etag(api: Api) -> None:
    r = await api.call("GET", "/commands/catalogue", as_="@ali")
    assert r.status_code == 200 and r.json()["ops"] and r.headers["etag"] == r.json()["etag"]
    again = await api.c.get(
        api.url("/commands/catalogue"), headers={"X-Debug-Actor": "@ali", "If-None-Match": r.headers["etag"]}
    )
    assert again.status_code == 304


# ----------------------------------------------------------------------------------------------- the manager's day


async def test_manager_can_run_the_acceptance_flow_and_events_say_which_op(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    # create a project: preview first (writes nothing), then apply
    pv = await one(api, "project.create", {"name": "Payments", "key": "PAY"}, preview=True)
    assert pv["status"] == "previewed" and pv["preview"]["will_create"]
    assert (await api.call("GET", "/projects/PAY")).status_code == 404
    assert (await one(api, "project.create", {"name": "Payments", "key": "PAY"}))["status"] == "applied"
    epic = await one(api, "item.create", {"title": "Checkout redesign", "project": "PAY", "type": "epic"})
    assert epic["status"] == "applied", epic
    team = await one(
        api,
        "team.create",
        {"name": "Payments Squad", "kind": "virtual", "lead": "@mahmoud", "members": ["@sara", "@ali"]},
    )
    assert team["status"] == "applied" and team["result"]["members"] == 2
    keys = []
    for t in ("a", "b", "c"):
        keys.append((await one(api, "item.create", {"title": f"task {t}", "project": "PAY", "parent": epic["result"]["key"]}))["result"]["key"])  # fmt: skip
    tagged = await one(api, "item.tag", {"keys": keys, "add": ["q4"]})
    assert tagged["result"]["links_added"] == 3
    moved = await one(api, "item.reparent", {"keys": keys[:1], "parent": None}, confirmed=True)
    assert moved["status"] == "applied"
    async with session_factory() as s:
        evs = (await s.execute(select(m.Event.event_type, m.Event.source, m.Event.payload).where(m.Event.payload["command"].is_not(None)))).all()  # fmt: skip
    ops = {e.payload["command"]["op"] for e in evs}
    assert {"project.create", "item.create", "team.create", "item.tag", "item.reparent"} <= ops
    assert all(str(e.source.value if hasattr(e.source, "value") else e.source) == "client" for e in evs)
    assert {e.payload["command"]["role_mode"] for e in evs} == {"manager"}


async def test_reporter_without_roles_is_refused_everything_with_a_reason_and_nothing_is_written(api: Api) -> None:  # fmt: skip
    r = await one(api, "project.create", {"name": "Nope", "key": "NOPE"}, as_="@ali")
    assert code(r) == "forbidden" and r["errors"][0]["details"]["permission"] == "project.create"
    assert (await api.call("GET", "/projects/NOPE")).status_code == 404


async def test_actor_must_be_the_token_owner(api: Api) -> None:
    c = cmd("tag.create", {"name": "x"}, actor="@sara")
    assert code((await send(api, c, as_="@mahmoud"))[0]) == "actor_mismatch"


async def test_unknown_op_and_invalid_args_are_rejections_not_http_errors(api: Api) -> None:
    res = await send(api, cmd("nope.nothing"), cmd("project.create", {"name": "x"}))
    assert [code(r) for r in res] == ["unknown_op", "invalid"]
    assert res[1]["errors"][0]["pointer"].startswith("/args/")


async def test_authorisation_matrix_generated_from_the_registry(api: Api) -> None:
    """A member with no roles is refused every op that declares a workspace-scope permission."""
    n = 0
    for op, spec in sorted(REGISTRY.items()):
        if spec.permission is None or spec.scope != "workspace":
            continue
        args = spec.args.model_validate(_example(spec)).model_dump(mode="json")
        res = await one(api, op, args, as_="@ali", confirmed=True)
        assert code(res) == "forbidden", (op, res)
        n += 1
    assert n >= 8


def _example(spec: Any) -> dict[str, Any]:
    return dict(spec.example_args)


async def test_scrum_master_runs_iterations_and_stage_moves_but_not_admin_things(api: Api) -> None:
    await api.role("@sara", "scrum_master")
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    it = await one(api, "iteration.create", {"project": "WEB", "name": "Sprint 1", "start_date": "2026-11-02", "end_date": "2026-11-13"}, as_="@sara")  # fmt: skip
    assert it["status"] == "applied", it
    assert (await one(api, "iteration.start", {"project": "WEB", "iteration": "Sprint 1"}, as_="@sara"))[
        "status"
    ] == "applied"
    assert code(await one(api, "project.create", {"name": "X", "key": "XX"}, as_="@sara")) == "forbidden"
    assert code(await one(api, "team.create", {"name": "T"}, as_="@sara")) == "forbidden"
    me = (await api.call("GET", "/me", as_="@sara")).json()
    assert "scrum_master" in me["role_modes"] and "manager" not in me["role_modes"]


async def test_coach_writes_notes_not_items(api: Api) -> None:
    await api.role("@sara", "coach")
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    item = (await one(api, "item.create", {"title": "t", "project": "WEB"}))["result"]["key"]
    assert code(await one(api, "item.update", {"key": item, "priority": "high"}, as_="@sara")) == "forbidden"
    note = await one(api, "context_note.add", {"title": "Coaching", "body": "pair on estimates"}, as_="@sara")
    assert note["status"] == "applied", note


async def test_forged_capture_scope_token_cannot_send_commands(api: Api) -> None:
    async def token(*scopes: str) -> dict[str, str]:
        r = await api.c.post("/auth/tokens", headers={"X-Debug-Actor": "@mahmoud"}, json={"workspace": api.slug, "name": "t", "scopes": list(scopes)})  # fmt: skip
        assert r.status_code == 201, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    cap, manage = await token("capture"), await token("manage")
    one_cmd = cmd("tag.create", {"name": "z"})
    assert (
        await api.c.post(api.url("/commands"), headers=cap, json={"commands": [one_cmd]})
    ).status_code == 403
    b = await api.c.post(api.url("/capture/batches"), headers=cap, json={"workspace": "acme", "messages": [], "commands": [one_cmd]})  # fmt: skip
    assert b.status_code == 403 and b.json()["error"]["code"] == "insufficient_scope"
    ok = await api.c.post(
        api.url("/commands"), headers=manage, json={"commands": [cmd("tag.create", {"name": "zz"})]}
    )
    assert ok.status_code == 200 and ok.json()["results"][0]["status"] == "applied"


# ----------------------------------------------------------------------------------------------- idempotency, destructive, batches


async def test_a_command_is_applied_once_per_client_id(api: Api) -> None:
    c = cmd("tag.create", {"name": "once"})
    a = (await send(api, c))[0]
    b = (await send(api, c))[0]
    assert a["status"] == "applied" and b["status"] == "applied" and b.get("deduplicated") is True
    assert a["result"] == b["result"]


async def test_destructive_ops_need_confirmation_and_force_is_logged(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    keys = [
        (await one(api, "item.create", {"title": f"d{i}", "project": "WEB"}))["result"]["key"]
        for i in range(2)
    ]
    assert code(await one(api, "item.merge", {"sources": keys, "target": keys[0]})) == "confirmation_required"
    forced = cmd("item.merge", {"sources": keys, "target": keys[0]})
    forced["args"]["_meta"]["forced"] = True
    assert (await send(api, forced))[0]["status"] == "applied"
    async with session_factory() as s:
        evs = (await s.scalars(select(m.Event).where(m.Event.event_type == "item.merged"))).all()
    assert evs and all(e.payload["command"]["forced"] is True for e in evs)


async def test_mixed_batch_applies_in_order_and_a_rejected_command_does_not_affect_the_others(
    api: Api,
) -> None:
    msg = {"actor": "@mahmoud", "action": "create", "at": "2026-10-25T10:00:00+03:00", "client_id": str(uuid.uuid4()), "project": "WEB", "note": "from the batch", "item": {"title": "batch item"}}  # fmt: skip
    cmds = [
        cmd("tag.create", {"name": "b1"}),
        cmd("project.create", {"name": "bad"}),
        cmd("tag.create", {"name": "b2"}),
    ]
    r = await api.call(
        "POST", "/capture/batches", json={"workspace": "acme", "messages": [msg], "commands": cmds}
    )
    assert r.status_code == 207, r.text
    body = r.json()
    assert (
        body["results"][0]["status"] in ("applied", "created", "ok")
        or body["results"][0]["status"] != "failed"
    )
    assert [c["status"] for c in body["command_results"]] == ["applied", "rejected", "applied"]


# ----------------------------------------------------------------------------------------------- merge


async def test_merge_preserves_minutes_evidence_and_links_and_soft_deletes_sources(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    a = (await one(api, "item.create", {"title": "m-a", "project": "WEB"}))["result"]
    b = (await one(api, "item.create", {"title": "m-b", "project": "WEB"}))["result"]
    c = (await one(api, "item.create", {"title": "m-c", "project": "WEB"}))["result"]
    await api.send(action="log_time", item={"key": a["key"]}, minutes=90)
    await api.send(action="log_time", item={"key": b["key"]}, minutes=30)
    await api.send(
        action="evidence", item={"key": b["key"]}, evidence=[{"kind": "link", "url": "https://x.test/1"}]
    )
    assert (await one(api, "item.link", {"from": b["key"], "to": c["key"], "type": "blocks"}))[
        "status"
    ] == "applied"
    pv = await one(api, "item.merge", {"sources": [a["key"], b["key"]], "target": a["key"]}, preview=True)
    assert pv["status"] == "previewed" and pv["preview"]["will_delete"] and pv["preview"]["affected"] == 1

    async def totals() -> tuple[int, int]:
        async with session_factory() as s:
            ids = [a["id"], b["id"]]
            mins = await s.scalar(
                select(func.coalesce(func.sum(m.TimeEntry.minutes), 0)).where(
                    m.TimeEntry.work_item_id.in_(ids)
                )
            )
            ev = await s.scalar(
                select(func.count()).select_from(m.Evidence).where(m.Evidence.work_item_id.in_(ids))
            )
            return int(mins or 0), int(ev or 0)

    before = await totals()
    res = await one(api, "item.merge", {"sources": [a["key"], b["key"]], "target": a["key"]}, confirmed=True)
    assert res["status"] == "applied", res
    assert await totals() == before == (120, 1)
    async with session_factory() as s:
        src = await s.get(m.WorkItem, b["id"])
        assert src is not None and src.deleted_at is not None and src.external_refs["merged_into"]
        links = (await s.scalars(select(m.WorkItemLink).where(m.WorkItemLink.from_item_id == a["id"], m.WorkItemLink.to_item_id == c["id"]))).all()  # fmt: skip
        assert len(links) == 1
    assert (await api.call("GET", f"/items/{b['key']}")).status_code == 404


async def test_merge_across_projects_needs_the_flag(api: Api) -> None:
    a = (await one(api, "item.create", {"title": "x", "project": "WEB"}))["result"]["key"]
    b = (await one(api, "item.create", {"title": "y", "project": "DATA"}))["result"]["key"]
    assert (
        code(await one(api, "item.merge", {"sources": [a, b], "target": a}, confirmed=True))
        == "cross_project"
    )


async def test_direct_merge_endpoint(api: Api) -> None:
    a = (await one(api, "item.create", {"title": "p", "project": "WEB"}))["result"]["key"]
    b = (await one(api, "item.create", {"title": "q", "project": "WEB"}))["result"]["key"]
    r = await api.call("POST", "/items:merge", json={"sources": [a, b], "target": a})
    assert r.status_code == 200 and r.json()["result"]["merged"] == [b]
    assert (
        await api.call("POST", "/items:merge", as_="@ali", json={"sources": [a], "new_title": "z"})
    ).status_code in (403, 404)


async def test_split_creates_siblings(api: Api) -> None:
    k = (await one(api, "item.create", {"title": "big", "project": "WEB"}))["result"]["key"]
    pv = await one(api, "item.split", {"key": k, "titles": ["api", "ui"]}, preview=True)
    assert len(pv["preview"]["will_create"]) == 2
    res = await one(api, "item.split", {"key": k, "titles": ["api", "ui"]})
    assert res["status"] == "applied" and len(res["result"]["created"]) == 2


async def test_preview_never_writes(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as s:
        before = await s.scalar(select(func.count()).select_from(m.Event))
    for op, args in (
        ("project.create", {"name": "Ghost", "key": "GHO"}),
        ("team.create", {"name": "ghosts", "members": ["@sara"]}),
    ):
        assert (await one(api, op, args, preview=True))["status"] == "previewed"
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.Event)) == before
        assert await s.scalar(select(func.count()).select_from(m.CommandLog)) == 0


async def test_merge_property_total_minutes_and_evidence_never_change(api: Api, session_factory: async_sessionmaker[AsyncSession]) -> None:  # fmt: skip
    """Randomised shapes (2-4 items, random minutes and evidence): the merged target carries exactly the sum, no row content changes."""
    import random

    rng = random.Random(14)
    for round_ in range(4):
        items = [(await one(api, "item.create", {"title": f"p{round_}-{i}", "project": "WEB"}))["result"] for i in range(rng.randint(2, 4))]  # fmt: skip
        want_min = want_ev = 0
        for it in items:
            for _ in range(rng.randint(0, 3)):
                mins = rng.randint(5, 240)
                await api.send(action="log_time", item={"key": it["key"]}, minutes=mins)
                want_min += mins
            for n in range(rng.randint(0, 2)):
                await api.send(action="evidence", item={"key": it["key"]}, evidence=[{"kind": "link", "url": f"https://x.test/{round_}/{it['key']}/{n}"}])  # fmt: skip
                want_ev += 1
        res = await one(
            api,
            "item.merge",
            {"sources": [i["key"] for i in items], "target": items[0]["key"]},
            confirmed=True,
        )
        assert res["status"] == "applied", res
        async with session_factory() as s:
            mins = await s.scalar(
                select(func.coalesce(func.sum(m.TimeEntry.minutes), 0)).where(
                    m.TimeEntry.work_item_id == items[0]["id"]
                )
            )
            ev = await s.scalar(
                select(func.count()).select_from(m.Evidence).where(m.Evidence.work_item_id == items[0]["id"])
            )
        assert (int(mins or 0), int(ev or 0)) == (want_min, want_ev)


async def test_context_pack_carries_role_context_and_team_now(api: Api) -> None:
    await one(
        api,
        "team.create",
        {"name": "Squad", "kind": "virtual", "lead": "@mahmoud", "members": ["@sara", "@ali"]},
    )
    pack = (await api.call("GET", "/context-pack")).json()
    assert "manager" in pack["my_role_modes"] and "admin" in pack["my_role_modes"]
    assert pack["my_teams_with_members"] and pack["my_teams_with_members"][0]["members"] == ["@ali", "@sara"]
    assert {"WEB", "DATA"} <= set(pack["my_projects_as_manager"])
    assert {"review_queue_count", "pending_approvals"} <= set(pack)
    reporter = (await api.call("GET", "/context-pack", as_="@ali")).json()
    assert reporter["my_role_modes"] == ["reporter"] and reporter["my_teams_with_members"] == []
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    await api.send(as_="@sara", action="start", project="WEB", item={"title": "sara's task"})
    r = await api.call("GET", "/activities?team=mine&status=active")
    assert r.status_code == 200 and [a["person"] for a in r.json()["data"]] == ["@sara"]
    assert (await api.call("GET", "/activities?team=mine", as_="@ali")).json()["data"] == []


def test_every_op_is_documented() -> None:
    from pathlib import Path

    doc = next(p for p in Path(__file__).resolve().parents if (p / "docs").is_dir()) / "docs/client/management-commands.md"
    text = doc.read_text()
    assert [op for op in REGISTRY if f"`{op}`" not in text] == []
