from __future__ import annotations

import json

from sqlalchemy import select
from tt_server.db import models as m

from tests.api.conftest import Api


async def _pid(api: Api, handle: str) -> str:
    return next(
        p["person_id"] for p in (await api.call("GET", "/people")).json()["data"] if p["handle"] == handle
    )


async def test_context_pack_shape_and_etag(api: Api) -> None:
    await api.send(
        action="create", item={"title": "fix the login redirect loop"}, project="WEB", tags=["auth"]
    )
    ms = await api.call("POST", "/projects/WEB/milestones", json={"name": "Beta", "due_date": "2999-11-10"})
    assert ms.status_code == 201
    await api.call("POST", "/translator-rules", json={"text": "prod incident -> priority critical"})
    r = await api.call("GET", "/context-pack")
    assert r.status_code == 200
    pack = r.json()
    assert pack["context_schema_version"] == 1 and pack["actor"] == "@mahmoud" and pack["workspace"] == "acme"
    assert {p["key"] for p in pack["projects"]} == {"WEB", "DATA"}  # owner of both
    assert [i["key"] for i in pack["my_open_items"]] == ["WEB-1"]
    assert pack["milestones"][0]["name"] == "Beta"
    assert pack["manager_rules"] == ["prod incident -> priority critical"]
    assert (
        pack["priorities"][0] == "lowest" and list(pack["stages"]) == ["sw-delivery"] and pack["risks"] == []
    )
    assert pack["manual_mode"]["required_fields"] == ["action", "item", "project"]
    assert "private" in r.headers["cache-control"]
    tag = r.headers["etag"]
    again = await api.c.get(
        api.url("/context-pack"), headers={"X-Debug-Actor": "@mahmoud", "If-None-Match": tag}
    )
    assert again.status_code == 304
    await api.call("POST", "/translator-rules", json={"text": "another rule"})
    changed = await api.c.get(
        api.url("/context-pack"), headers={"X-Debug-Actor": "@mahmoud", "If-None-Match": tag}
    )
    assert changed.status_code == 200 and changed.headers["etag"] != tag


async def test_context_pack_is_personalised_and_small(api: Api) -> None:
    await api.send(as_="@sara", action="create", item={"title": "sara only"}, project="DATA")
    mine = (await api.call("GET", "/context-pack")).json()
    sara = (await api.call("GET", "/context-pack", as_="@sara")).json()
    assert [i["key"] for i in sara["my_open_items"]] == ["DATA-1"]
    assert mine["my_open_items"] == []
    assert len(json.dumps(sara)) < 30_000


async def test_provisional_tags_stay_out_of_the_pack(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    await api.send(action="create", item={"title": "t"}, project="WEB", tags=["fresh"])
    assert (await api.call("GET", "/context-pack")).json()["tags"] == []
    async with session_factory() as s:
        tag = (await s.scalars(select(m.Tag))).one()
        tag.is_provisional = False
        await s.commit()
    assert (await api.call("GET", "/context-pack")).json()["tags"] == ["fresh"]


async def test_glossary_crud_and_resolution(api: Api) -> None:
    data = (await api.call("GET", "/projects/DATA")).json()
    created = await api.call(
        "POST",
        "/glossary",
        json={"phrase": "the ingest thing", "resolves_to_type": "project", "resolves_to_id": data["id"]},
    )
    assert created.status_code == 201
    dup = await api.call(
        "POST",
        "/glossary",
        json={"phrase": "The Ingest Thing", "resolves_to_type": "project", "resolves_to_id": data["id"]},
    )
    assert dup.status_code == 409
    res = await api.send(action="create", item={"title": "glossary routed"}, project="the ingest thing")
    assert res["item"]["project_key"] == "DATA" and res["resolution"]["project"]["method"] == "glossary"
    pack = (await api.call("GET", "/context-pack")).json()
    assert pack["glossary"]["the ingest thing"] == "PROJECT:DATA"
    assert "the ingest thing" in next(p for p in pack["projects"] if p["key"] == "DATA")["aliases"]
    entry = (await api.call("GET", "/glossary")).json()["data"][0]
    assert entry["hits"] == 1
    assert (await api.call("DELETE", f"/glossary/{entry['id']}")).status_code == 204
    assert (await api.call("GET", "/glossary")).json()["data"] == []


async def test_translator_rules_crud_and_scoping(api: Api) -> None:
    web = (await api.call("GET", "/projects/WEB")).json()
    bad = await api.call("POST", "/translator-rules", json={"text": "x", "scope_type": "project"})
    assert bad.status_code == 400
    ws_rule = (await api.call("POST", "/translator-rules", json={"text": "ws rule", "position": 2})).json()
    await api.call(
        "POST",
        "/translator-rules",
        json={"text": "web rule", "scope_type": "project", "scope_id": web["id"], "position": 1},
    )
    await api.send(action="create", item={"title": "touch web"}, project="WEB")
    assert (await api.call("GET", "/context-pack")).json()["manager_rules"] == ["web rule", "ws rule"]
    await api.call("PATCH", f"/translator-rules/{ws_rule['id']}", json={"is_active": False})
    assert (await api.call("GET", "/context-pack")).json()["manager_rules"] == ["web rule"]
    assert (await api.call("DELETE", f"/translator-rules/{ws_rule['id']}")).status_code == 204
    assert len((await api.call("GET", "/translator-rules")).json()["data"]) == 1


async def test_feedback_is_stored(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    r = await api.call(
        "POST",
        "/capture/feedback",
        json=[{"client_id": "c1", "field": "minutes", "raw": "took 2h", "previous": 120, "corrected": 90}],
    )
    assert r.status_code == 201 and r.json() == {"stored": 1}
    async with session_factory() as s:
        row = (await s.scalars(select(m.TranslatorFeedback))).one()
    assert (row.field, row.previous, row.corrected) == ("minutes", 120, 90)


async def test_activities_endpoint(api: Api) -> None:
    await api.send(action="start", item={"title": "now working"}, project="WEB")
    rows = (await api.call("GET", "/activities", params={"status": "active", "person": "me"})).json()["data"]
    assert len(rows) == 1 and rows[0]["item_key"] == "WEB-1" and rows[0]["item_title"] == "now working"


async def test_batch_mode_and_jira_deferred(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    body = {
        "workspace": "acme",
        "mode": "manual",
        "client_version": "0.1",
        "messages": [
            {
                "actor": "@mahmoud",
                "action": "create",
                "at": "2026-10-23T10:00:00Z",
                "client_id": "jira-1",
                "item": {"title": "send to jira"},
                "project": "WEB",
                "jira": {"project": "WEB", "issuetype": "Task", "send": True},
            }
        ],
    }
    r = await api.call("POST", "/capture/batches", json=body)
    assert r.status_code == 200 and r.json()["results"][0]["jira"] == {"status": "deferred"}
    async with session_factory() as s:
        ev = await s.scalar(select(m.Event).where(m.Event.client_id == "jira-1"))
    assert ev is not None and ev.payload["mode"] == "manual" and ev.payload["jira"]["send"] is True
    bad = await api.call("POST", "/capture/batches", json={**body, "mode": "voice"})
    assert bad.status_code == 422
