"""Console endpoints (Phase 5; router: console)."""

from __future__ import annotations

from typing import Any

import httpx

from tests.api.conftest import Api

ROUTERS_COVERED = ("console", "webauth")


async def test_review_stream_sends_the_queue_and_is_scoped(api: Api) -> None:
    await api.send(
        as_="@sara", action="create", item={"title": "fix the billing thing"}, project="the billing thing"
    )
    async with api.c.stream(
        "GET", api.url("/review-queue/stream"), params={"once": "true"}, headers={"X-Debug-Actor": "@mahmoud"}
    ) as r:
        body = (await r.aread()).decode()
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    assert "event: queue" in body and '"count": 1' in body and "review_item:" in body
    ali = await api.c.get(
        api.url("/review-queue/stream"), params={"once": "true"}, headers={"X-Debug-Actor": "@ali"}
    )
    assert ali.status_code == 403  # not anyone's manager


async def test_stage_reorder_validates_and_audits(api: Api) -> None:
    procs = (await api.call("GET", "/processes")).json()["data"]
    p = procs[0]
    keys = [s["key"] for s in p["stages"]]
    assert len(keys) >= 2
    bad = await api.call("PATCH", f"/processes/{p['key']}/stages:reorder", json={"order": keys[:-1]})
    assert bad.status_code == 400
    new = list(reversed(keys))
    ok = await api.call("PATCH", f"/processes/{p['key']}/stages:reorder", json={"order": new})
    assert ok.status_code == 200 and [s["key"] for s in ok.json()["stages"]] == new
    again = (await api.call("GET", "/processes")).json()["data"][0]
    assert [s["key"] for s in again["stages"]] == new
    assert (
        await api.call("PATCH", f"/processes/{p['key']}/stages:reorder", as_="@ali", json={"order": new})
    ).status_code == 403
    ev = (await api.call("GET", "/events", params={"event_type": "stage.reordered"})).json()["data"]
    assert ev and ev[0]["payload"]["after"] == new


async def test_dry_run_needs_the_gateway_and_uses_visible_lines(api: Api) -> None:
    await api.send(action="create", item={"title": "alpha"}, project="WEB", raw_input="alpha thing for web")
    off = await api.call("POST", "/translator-rules:dry-run", json={"rules": ["x"]})
    assert off.status_code == 200 and off.json()["available"] is False
    seen: list[Any] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(req.content))
        return httpx.Response(
            200,
            json={
                "output": {
                    "checked": 1,
                    "changed": 1,
                    "results": [{"line": "alpha thing for web", "fields": ["priority"]}],
                },
                "run_id": "r",
                "warnings": [],
            },
        )

    from tt_server.integrations.llm import LLMGatewayClient

    api.c._transport.app.state.llm = LLMGatewayClient(
        "http://gw", client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gw")
    )  # type: ignore[attr-defined]
    on = await api.call("POST", "/translator-rules:dry-run", json={"rules": ["prod -> critical"]})
    assert (
        on.json()["changed"] == 1
        and seen[0]["lines"] == ["alpha thing for web"]
        and seen[0]["rules"] == ["prod -> critical"]
    )
    assert (
        await api.call("POST", "/translator-rules:dry-run", as_="@ali", json={"rules": []})
    ).status_code == 403


async def test_context_pack_for_another_person_is_admin_only(api: Api) -> None:
    ok = await api.call("GET", "/context-pack", params={"for": "@sara"})
    assert ok.status_code == 200 and ok.json()["actor"] == "@sara"
    assert (await api.call("GET", "/context-pack", as_="@ali", params={"for": "@sara"})).status_code == 403
    assert (await api.call("GET", "/context-pack", params={"for": "@nobody"})).status_code == 404


async def test_item_events_and_me(api: Api) -> None:
    it = (await api.call("POST", "/items", json={"title": "audited", "project": "WEB"})).json()
    ev = (await api.call("GET", f"/items/{it['key']}/events")).json()["data"]
    assert any(e["event_type"] == "item.created" for e in ev)
    assert (await api.call("GET", f"/items/{it['key']}/events", as_="@ali")).status_code == 404
    me = (await api.call("GET", "/me")).json()
    assert me["handle"] == "@mahmoud" and me["is_admin"] and me["workspace"]["slug"] == "acme"


async def test_my_workspaces_by_token(api: Api) -> None:
    tok = (
        await api.c.post("/auth/tokens", headers={"X-Debug-Actor": "@mahmoud"}, json={"workspace": "acme"})
    ).json()["token"]
    r = await api.c.get("/api/v1/me/workspaces", headers={"Authorization": f"Bearer {tok}"})
    assert r.json()["data"] == [{"slug": "acme", "name": "Acme", "handle": "@mahmoud"}]
    assert (await api.c.get("/api/v1/me/workspaces")).status_code == 401
