"""Visibility inside the query compiler and permissions on saved views (Phase 6; router: query)."""

from __future__ import annotations

from tests.api.conftest import Api

ROUTERS_COVERED = ("query",)


async def test_queries_only_return_what_the_caller_may_see(api: Api) -> None:
    secret = (await api.call("POST", "/items", json={"title": "wip secret", "project": "WEB"})).json()["key"]
    mine = await api.call("POST", "/query/items", json={"filter": {"text": "secret"}})
    assert [i["key"] for i in mine.json()["items"]] == [secret]
    ali = await api.call(
        "POST", "/query/items", as_="@ali", json={"filter": {"text": "secret"}}
    )  # not a WEB member
    assert ali.json()["items"] == [] and ali.json()["page"]["total"] == 0
    agg = await api.call("POST", "/query/aggregate", as_="@ali", json={"rows": "project"})
    assert agg.json()["totals"]["all"] == 0
    board = await api.call("GET", "/projects/WEB/board", as_="@ali")
    assert board.status_code == 404  # the project itself is invisible


async def test_inbox_items_never_leak_through_queries_or_exports(api: Api) -> None:
    people = {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}
    line = {"person_id": people["@sara"], "manager_id": people["@mahmoud"], "kind": "line"}
    assert (await api.call("POST", "/reporting-lines", json=line)).status_code == 201
    await api.send(as_="@sara", action="create", item={"title": "sara private thought"}, project="nowhere")
    body = {"filter": {"include_inbox": True, "text": "private thought"}}
    seen = {
        w: len((await api.call("POST", "/query/items", as_=w, json=body)).json()["items"])
        for w in ("@sara", "@mahmoud", "@ali")
    }
    assert seen == {"@sara": 1, "@mahmoud": 1, "@ali": 0}  # owner and line manager only
    ali_csv = await api.call("POST", "/query/items:csv", as_="@ali", json={"filter": {"include_inbox": True}})
    assert "private thought" not in ali_csv.text
    assert (
        "private thought"
        in (
            await api.call("POST", "/query/items:csv", as_="@sara", json={"filter": {"include_inbox": True}})
        ).text
    )


async def test_sharing_a_view_needs_view_share_and_only_owners_edit(api: Api) -> None:
    ok = await api.call("POST", "/views", as_="@sara", json={"name": "mine", "filter": {}})
    assert ok.status_code == 201
    denied = await api.call(
        "POST", "/views", as_="@sara", json={"name": "shared", "filter": {}, "is_shared": True}
    )
    assert denied.status_code == 403 and denied.json()["error"]["details"]["permission"] == "view.share"
    await api.role("@sara", "manager")
    shared = await api.call(
        "POST", "/views", as_="@sara", json={"name": "shared", "filter": {}, "is_shared": True}
    )
    assert shared.status_code == 201
    vid = shared.json()["id"]
    assert (await api.call("PATCH", f"/views/{vid}", as_="@ali", json={"name": "hijack"})).status_code == 403
    assert (await api.call("DELETE", f"/views/{vid}", as_="@ali")).status_code == 403
    assert (await api.call("DELETE", f"/views/{vid}")).status_code == 204  # an admin may remove it


async def test_explain_is_admin_only(api: Api) -> None:
    assert (await api.call("POST", "/query/explain", as_="@ali", json={})).status_code == 403
    assert (
        "SELECT"
        in (await api.call("POST", "/query/explain", json={"filter": {"priority": {"gte": "high"}}})).json()[
            "sql"
        ]
    )
