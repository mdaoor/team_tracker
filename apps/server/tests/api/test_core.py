from __future__ import annotations

from sqlalchemy import select
from tt_server.db import models as m

from tests.api.conftest import Api


async def test_token_auth_flow(api: Api, client) -> None:  # type: ignore[no-untyped-def]
    r = await client.post(
        "/auth/tokens", headers={"X-Debug-Actor": "@sara"}, json={"workspace": "acme", "name": "laptop"}
    )
    assert r.status_code == 201
    token = r.json()["token"]
    assert token.startswith("tt_")
    ok = await client.get("/api/v1/w/acme/people", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200 and len(ok.json()["data"]) == 3
    bad = await client.get("/api/v1/w/acme/people", headers={"Authorization": "Bearer tt_nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "invalid_token"
    none = await client.get("/api/v1/w/acme/people")
    assert none.status_code == 401
    # a token never works for another workspace
    other = await client.get("/api/v1/w/other/people", headers={"Authorization": f"Bearer {token}"})
    assert other.status_code == 404


async def test_token_is_stored_hashed(api: Api, client, session_factory) -> None:  # type: ignore[no-untyped-def]
    r = await client.post("/auth/tokens", headers={"X-Debug-Actor": "@sara"}, json={"workspace": "acme"})
    async with session_factory() as s:
        row = (await s.scalars(select(m.ApiToken))).one()
    assert r.json()["token"] not in row.hash and len(row.hash) == 64


async def test_non_member_gets_404(api: Api, client) -> None:  # type: ignore[no-untyped-def]
    r = await client.get("/api/v1/w/acme/people", headers={"X-Debug-Actor": "@stranger"})
    assert r.status_code == 401


async def test_workspace_creation_is_dev_only(client) -> None:  # type: ignore[no-untyped-def]
    client._transport.app.state.settings = client._transport.app.state.settings.model_copy(
        update={"dev_auth": False}
    )
    r = await client.post(
        "/api/v1/workspaces",
        json={"name": "x", "slug": "xx", "owner": {"display_name": "a", "email": "a@x.test", "handle": "@a"}},
    )
    assert r.status_code == 403


async def test_member_gets_private_inbox(api: Api) -> None:
    projects = (await api.call("GET", "/projects")).json()["data"]
    assert {p["key"] for p in projects} == {"DATA", "WEB"}  # inboxes are not listed
    inbox = (await api.call("GET", "/projects/INBOX-sara")).json()
    assert inbox["kind"] == "inbox" and inbox["visibility"] == "private"


async def test_project_validation_and_duplicates(api: Api) -> None:
    dup = await api.call("POST", "/projects", json={"name": "Again", "key": "web"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "project_exists"
    bad = await api.call("POST", "/projects", json={"name": "Bad", "key": "bad key!"})
    assert bad.status_code == 400
    missing = await api.call("POST", "/projects", json={"name": "No key"})
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "validation_error"


async def test_item_keys_are_sequential_per_project(api: Api) -> None:
    keys = []
    for t in ("a", "b", "c"):
        r = await api.call("POST", "/items", json={"title": t, "project": "WEB"})
        assert r.status_code == 201
        keys.append(r.json()["key"])
    assert keys == ["WEB-1", "WEB-2", "WEB-3"]
    assert (await api.call("POST", "/items", json={"title": "d", "project": "DATA"})).json()[
        "key"
    ] == "DATA-1"


async def test_nesting_rules_and_subtree_move(api: Api) -> None:
    epic = (
        await api.call(
            "POST", "/items", json={"title": "e", "project": "WEB", "type_id": await _type(api, "epic")}
        )
    ).json()
    story = (
        await api.call(
            "POST",
            "/items",
            json={
                "title": "s",
                "project": "WEB",
                "type_id": await _type(api, "story"),
                "parent": epic["key"],
            },
        )
    ).json()
    task = (
        await api.call("POST", "/items", json={"title": "t", "project": "WEB", "parent": story["key"]})
    ).json()
    assert task["path"].split(".")[:2] == story["path"].split(".")
    # a task cannot contain an epic
    bad = await api.call(
        "POST",
        "/items",
        json={"title": "x", "project": "WEB", "type_id": await _type(api, "epic"), "parent": task["key"]},
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_nesting"
    # moving the story to the root rewrites the child's path
    moved = await api.call("POST", f"/items/{story['key']}/move", json={"parent": None})
    assert moved.status_code == 200
    child = (await api.call("GET", f"/items/{task['key']}")).json()
    assert child["path"] == f"{moved.json()['path']}.{child['path'].split('.')[-1]}"
    cyc = await api.call("POST", f"/items/{story['key']}/move", json={"parent": task["key"]})
    assert cyc.status_code == 400


async def _type(api: Api, key: str) -> str:
    from tests.api import conftest  # noqa: F401

    async with api.c._transport.app.state.session_factory() as s:
        return str(await s.scalar(select(m.WorkItemType.id).where(m.WorkItemType.key == key)))


async def test_state_endpoint_and_lists(api: Api) -> None:
    it = (await api.call("POST", "/items", json={"title": "listed", "project": "WEB"})).json()
    r = await api.call("POST", f"/items/{it['key']}/state", json={"state": "doing"})
    assert r.status_code == 200 and r.json()["actual_start"] is not None
    assert (await api.call("POST", f"/items/{it['key']}/state", json={"state": "nope"})).status_code == 400
    mine = (
        await api.call(
            "GET",
            "/items",
            params={"assignee": "me", "state_category": "open,active", "fields": "key,title,project_key"},
        )
    ).json()
    assert mine["data"] == [{"key": it["key"], "title": "listed", "project_key": "WEB"}]
    done = (await api.call("GET", "/items", params={"state_category": "done"})).json()
    assert done["data"] == []


async def test_pagination_cursor(api: Api) -> None:
    for i in range(5):
        await api.call("POST", "/items", json={"title": f"p{i}", "project": "WEB"})
    first = (await api.call("GET", "/items", params={"limit": 2})).json()
    assert len(first["data"]) == 2 and first["next_cursor"]
    second = (await api.call("GET", "/items", params={"limit": 2, "cursor": first["next_cursor"]})).json()
    assert {d["key"] for d in first["data"]}.isdisjoint({d["key"] for d in second["data"]})


async def test_iteration_state_machine(api: Api) -> None:
    it = (
        await api.call(
            "POST",
            "/projects/WEB/iterations",
            json={"name": "S1", "start_date": "2026-10-01", "end_date": "2026-10-14"},
        )
    ).json()
    it2 = (
        await api.call(
            "POST",
            "/projects/WEB/iterations",
            json={"name": "S2", "start_date": "2026-10-15", "end_date": "2026-10-28"},
        )
    ).json()
    assert (
        await api.call("POST", f"/iterations/{it['id']}/state", json={"state": "closed"})
    ).status_code == 409
    assert (
        await api.call("POST", f"/iterations/{it['id']}/state", json={"state": "active"})
    ).status_code == 200
    assert (
        await api.call("POST", f"/iterations/{it2['id']}/state", json={"state": "active"})
    ).status_code == 409
    assert (
        await api.call("POST", f"/iterations/{it['id']}/state", json={"state": "closed"})
    ).status_code == 200
    active = (await api.call("GET", "/iterations", params={"state": "active"})).json()["data"]
    assert active == []


async def test_reporting_line_rules(api: Api) -> None:
    people = {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}
    ok = await api.call(
        "POST",
        "/reporting-lines",
        json={"person_id": people["@sara"], "manager_id": people["@mahmoud"], "is_primary": True},
    )
    assert ok.status_code == 201
    dup = await api.call(
        "POST",
        "/reporting-lines",
        json={"person_id": people["@sara"], "manager_id": people["@ali"], "is_primary": True},
    )
    assert dup.status_code == 409
    bad = await api.call(
        "POST",
        "/reporting-lines",
        json={"person_id": people["@ali"], "manager_id": people["@sara"], "scope_type": "project"},
    )
    assert bad.status_code == 400


async def test_teams_effective_members(api: Api) -> None:
    people = {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}
    parent = (await api.call("POST", "/teams", json={"name": "Eng", "slug": "eng"})).json()
    child = (
        await api.call(
            "POST", "/teams", json={"name": "Squad", "slug": "squad", "parent_team_id": parent["id"]}
        )
    ).json()
    await api.call("POST", f"/teams/{parent['id']}/memberships", json={"person_id": people["@mahmoud"]})
    await api.call("POST", f"/teams/{child['id']}/memberships", json={"person_id": people["@sara"]})
    direct = (await api.call("GET", f"/teams/{parent['id']}/members")).json()["data"]
    effective = (
        await api.call("GET", f"/teams/{parent['id']}/members", params={"effective": "true"})
    ).json()["data"]
    assert len(direct) == 1 and len(effective) == 2


async def test_milestones_context_notes_baseline_tag_merge(api: Api) -> None:
    ms = await api.call("POST", "/projects/WEB/milestones", json={"name": "Beta", "due_date": "2999-01-01"})
    assert ms.status_code == 201
    assert (await api.call("GET", "/milestones", params={"upcoming": "90d"})).json()["data"] == []
    assert (await api.call("POST", f"/milestones/{ms.json()['id']}/complete")).json()["completed_at"]
    note = await api.call(
        "POST",
        "/context-notes",
        json={"scope_type": "workspace", "scope_id": "x", "title": "Goal", "body": "ship"},
    )
    assert note.status_code == 201
    assert (
        len((await api.call("GET", "/context-notes", params={"scope_type": "workspace"})).json()["data"]) == 1
    )
    await api.send(action="create", item={"title": "tagged"}, project="WEB", tags=["alpha", "beta"])
    tags = {t["name"]: t["id"] for t in (await api.call("GET", "/tags")).json()["data"]}
    merged = await api.call("POST", f"/tags/{tags['alpha']}/merge-into/{tags['beta']}")
    assert merged.status_code == 200 and merged.json()["links_moved"] == 0
    assert {t["name"] for t in (await api.call("GET", "/tags")).json()["data"]} == {"beta"}
    base = await api.call("POST", "/projects/WEB/baselines", params={"name": "v1"})
    assert base.status_code == 201 and base.json()["items"] == 1
