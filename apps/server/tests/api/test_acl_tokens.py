"""API token scopes: capture < read < manage < admin (Phase 4 §4.5, routers: auth, capture, client)."""

from __future__ import annotations

import pytest

from tests.api.conftest import Api

ROUTERS_COVERED = ("auth", "capture", "client")


async def _token(api: Api, *scopes: str) -> dict[str, str]:
    r = await api.c.post(
        "/auth/tokens",
        headers={"X-Debug-Actor": "@mahmoud"},
        json={"workspace": api.slug, "name": "t", "scopes": list(scopes)},
    )
    assert r.status_code == 201, r.text
    assert r.json()["scopes"] == list(scopes)
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _call(api: Api, hdr: dict[str, str], method: str, path: str, **kw: object) -> int:
    return (await api.c.request(method, api.url(path), headers=hdr, **kw)).status_code  # type: ignore[arg-type]


async def test_capture_token_can_post_batches_but_not_patch_items(api: Api) -> None:
    item = (await api.call("POST", "/items", json={"title": "x", "project": "WEB"})).json()
    hdr = await _token(api, "capture")
    body = {
        "workspace": "acme",
        "messages": [
            {
                "actor": "@mahmoud",
                "action": "create",
                "at": "2026-10-23T10:00:00Z",
                "client_id": "tok-1",
                "item": {"title": "from a capture token"},
                "project": "WEB",
            }
        ],
    }
    assert await _call(api, hdr, "POST", "/capture/batches", json=body) == 200
    assert await _call(api, hdr, "GET", "/context-pack") == 200
    assert await _call(api, hdr, "GET", "/activities", params={"person": "me"}) == 200
    assert await _call(api, hdr, "PATCH", f"/items/{item['key']}", json={"title": "renamed"}) == 403
    assert await _call(api, hdr, "GET", "/items") == 403
    assert await _call(api, hdr, "GET", "/activities", params={"person": "@sara"}) == 403


async def test_read_token_reads_everything_it_may_but_writes_nothing(api: Api) -> None:
    hdr = await _token(api, "read")
    assert await _call(api, hdr, "GET", "/items") == 200
    assert await _call(api, hdr, "GET", "/projects") == 200
    assert await _call(api, hdr, "POST", "/items", json={"title": "x", "project": "WEB"}) == 403
    assert await _call(api, hdr, "POST", "/capture/batches", json={}) in (200, 422)  # capture is below read


@pytest.mark.parametrize(
    ("scope", "item_write", "role_admin"),
    [("manage", 201, 403), ("admin", 201, 201)],
)
async def test_manage_and_admin_tokens(api: Api, scope: str, item_write: int, role_admin: int) -> None:
    hdr = await _token(api, scope)
    assert await _call(api, hdr, "POST", "/items", json={"title": "x", "project": "WEB"}) == item_write
    r = await _call(api, hdr, "POST", "/role-assignments", json={"handle": "@ali", "role": "viewer"})
    assert r == role_admin


async def test_invalid_scopes_are_rejected_and_tokens_cannot_exceed_their_owner(api: Api) -> None:
    r = await api.c.post(
        "/auth/tokens",
        headers={"X-Debug-Actor": "@mahmoud"},
        json={"workspace": "acme", "scopes": ["root"]},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_scope"
    # an admin-scoped token of a plain contributor is still just a contributor
    r = await api.c.post(
        "/auth/tokens",
        headers={"X-Debug-Actor": "@ali"},
        json={"workspace": "acme", "scopes": ["admin"]},
    )
    hdr = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (
        await _call(api, hdr, "POST", "/role-assignments", json={"handle": "@ali", "role": "workspace_admin"})
        == 403
    )


async def test_permission_denials_are_logged_not_evented(api: Api) -> None:
    import structlog
    from sqlalchemy import func, select
    from tt_server.db import models as m

    async def events(session_factory: object) -> int:
        async with session_factory() as s:  # type: ignore[operator]
            return int(await s.scalar(select(func.count()).select_from(m.Event)) or 0)

    sf = api.c._transport.app.state.session_factory  # type: ignore[attr-defined]
    before = await events(sf)
    with structlog.testing.capture_logs() as logs:
        r = await api.call("POST", "/dimensions", as_="@ali", json={"key": "region", "name": "Region"})
    assert r.status_code == 403
    denied = [e for e in logs if e["event"] == "acl_denied"]
    assert denied and denied[0]["permission"] == "dimension.admin" and denied[0]["actor"] == "@ali"
    assert await events(sf) == before  # denials are not domain events
