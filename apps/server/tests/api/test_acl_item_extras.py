"""Plan P1 companions: comments, checklists, links (cycle guard), custom fields, attachments (StorageAdapter on local disk)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.storage.adapter import LocalDiskStorage, StorageError

from tests.api.conftest import Api

ROUTERS_COVERED = ("item_extras",)


def blobs(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


async def raw(api: Api, method: str, path: str, *, content_type: str | None = None, **kw: Any) -> Any:
    """`Api.call` owns the headers kwarg; uploads need their own content-type."""
    headers = {"X-Debug-Actor": "@mahmoud", **({"content-type": content_type} if content_type else {})}
    return await api.c.request(method, api.url(path), headers=headers, **kw)


async def item(api: Api, title: str = "one", project: str = "WEB") -> str:
    r = await api.call("POST", "/items", json={"title": title, "project": project})
    assert r.status_code == 201, r.text
    return str(r.json()["key"])


async def events(sf: async_sessionmaker[AsyncSession], etype: str) -> int:
    async with sf() as s:
        return len((await s.scalars(select(m.Event).where(m.Event.event_type == etype))).all())


# ---------------------------------------------------------------- comments
async def test_comments_thread_edit_delete_with_events(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    k = await item(api)
    c = (await api.call("POST", f"/items/{k}/comments", json={"body": "first"})).json()
    r = await api.call("POST", f"/items/{k}/comments", json={"body": "reply", "parent_comment_id": c["id"]})
    assert r.status_code == 201 and r.json()["parent_comment_id"] == c["id"]
    assert [x["body"] for x in (await api.call("GET", f"/items/{k}/comments")).json()["data"]] == [
        "first",
        "reply",
    ]
    # only the author edits; an admin may delete
    assert (await api.call("PATCH", f"/comments/{c['id']}", as_="@sara", json={"body": "x"})).status_code in (
        403,
        404,
    )
    assert (await api.call("PATCH", f"/comments/{c['id']}", json={"body": "edited"})).json()["edited_at"]
    assert (await api.call("DELETE", f"/comments/{c['id']}")).status_code == 204
    assert [x["body"] for x in (await api.call("GET", f"/items/{k}/comments")).json()["data"]] == ["reply"]
    assert [
        await events(session_factory, e) for e in ("comment.created", "comment.edited", "comment.deleted")
    ] == [2, 1, 1]


async def test_comment_validation_and_cross_item_parent(api: Api) -> None:
    a, b = await item(api, "a"), await item(api, "b")
    c = (await api.call("POST", f"/items/{a}/comments", json={"body": "x"})).json()
    assert (
        await api.call("POST", f"/items/{b}/comments", json={"body": "y", "parent_comment_id": c["id"]})
    ).status_code == 400
    assert (await api.call("POST", f"/items/{a}/comments", json={"body": ""})).status_code in (400, 422)
    assert (await api.call("POST", "/items/WEB-9999/comments", json={"body": "x"})).status_code == 404


# ---------------------------------------------------------------- checklists
async def test_checklist_progress_and_done_stamp(api: Api) -> None:
    k = await item(api)
    cl = (
        await api.call("POST", f"/items/{k}/checklists", json={"title": "Release", "items": ["tag", "notes"]})
    ).json()
    assert cl["total"] == 2 and cl["done"] == 0
    first = cl["items"][0]["id"]
    done = (await api.call("PATCH", f"/checklist-items/{first}", json={"is_done": True})).json()
    assert done["is_done"] and done["done_by"] and done["done_at"]
    added = (await api.call("POST", f"/checklists/{cl['id']}/items", json={"text": "announce"})).json()
    assert added["position"] == 2
    listed = (await api.call("GET", f"/items/{k}/checklists")).json()["data"][0]
    assert (listed["done"], listed["total"]) == (1, 3)
    undone = (await api.call("PATCH", f"/checklist-items/{first}", json={"is_done": False})).json()
    assert undone["done_by"] is None
    assert (await api.call("DELETE", f"/checklist-items/{first}")).status_code == 204
    assert (await api.call("GET", f"/items/{k}/checklists")).json()["data"][0]["total"] == 2
    assert (await api.call("PATCH", "/checklist-items/nope", json={"is_done": True})).status_code == 404


# ---------------------------------------------------------------- links
async def test_links_reject_self_duplicates_and_cycles(api: Api) -> None:
    a, b, c = await item(api, "a"), await item(api, "b"), await item(api, "c")
    ok = await api.call(
        "POST", f"/items/{a}/links", json={"to": b, "link_type": "finish_to_start", "lag_days": 1}
    )
    assert ok.status_code == 201
    assert (await api.call("POST", f"/items/{a}/links", json={"to": a})).status_code == 400
    assert (
        await api.call("POST", f"/items/{a}/links", json={"to": b, "link_type": "finish_to_start"})
    ).status_code == 409
    assert (
        await api.call("POST", f"/items/{b}/links", json={"to": c, "link_type": "blocks"})
    ).status_code == 201
    # c -> a closes a -> b -> c -> a, however it is spelled
    for t, frm, to in (
        ("finish_to_start", c, a),
        ("blocks", c, a),
        ("depends_on", a, c),
        ("is_blocked_by", a, c),
    ):
        r = await api.call("POST", f"/items/{frm}/links", json={"to": to, "link_type": t})
        assert r.status_code == 409 and r.json()["error"]["code"] == "dependency_cycle", t
    # informational links never cycle
    assert (
        await api.call("POST", f"/items/{c}/links", json={"to": a, "link_type": "relates_to"})
    ).status_code == 201
    got = (await api.call("GET", f"/items/{b}/links")).json()["data"]
    assert {(x["direction"], x["other"]) for x in got} == {("in", a), ("out", c)}
    lid = got[0]["id"]
    assert (await api.call("DELETE", f"/links/{lid}")).status_code == 204
    assert (await api.call("DELETE", f"/links/{lid}")).status_code == 404


# ---------------------------------------------------------------- custom fields
async def test_custom_field_definitions_and_typed_values(api: Api) -> None:
    k = await item(api)
    defs = {
        "cost": {"name": "Cost", "key": "cost", "field_type": "number"},
        "due": {"name": "Due", "key": "due", "field_type": "date"},
        "env": {"name": "Env", "key": "env", "field_type": "select", "options": ["dev", "prod"]},
        "tags": {"name": "Tags", "key": "tg", "field_type": "multi_select", "options": ["a", "b"]},
        "ok": {"name": "OK", "key": "ok", "field_type": "bool"},
        "link": {"name": "Link", "key": "link", "field_type": "url"},
    }
    for d in defs.values():
        assert (await api.call("POST", "/custom-fields", json=d)).status_code == 201
    assert (await api.call("POST", "/custom-fields", json=defs["cost"])).status_code == 409
    assert (
        await api.call("POST", "/custom-fields", json={"name": "S", "key": "s", "field_type": "select"})
    ).status_code == 400
    assert (
        await api.call("POST", "/custom-fields", json={"name": "S", "key": "Bad Key", "field_type": "text"})
    ).status_code in (400, 422)
    good: dict[str, Any] = {
        "cost": 12.5,
        "due": "2026-11-01",
        "env": "prod",
        "tg": ["a"],
        "ok": True,
        "link": "https://x.test/y",
    }
    for key, v in good.items():
        r = await api.call("PUT", f"/items/{k}/custom-fields/{key}", json={"value": v})
        assert r.status_code == 200, (key, r.text)
    assert (await api.call("GET", f"/items/{k}/custom-fields")).json()["data"] == good
    for key, v in (
        ("cost", "12"),
        ("due", "tomorrow"),
        ("env", "stage"),
        ("tg", ["z"]),
        ("ok", "yes"),
        ("link", "ftp://x"),
    ):
        assert (
            await api.call("PUT", f"/items/{k}/custom-fields/{key}", json={"value": v})
        ).status_code == 400, key
    assert (await api.call("PUT", f"/items/{k}/custom-fields/nope", json={"value": 1})).status_code == 404
    assert (await api.call("PUT", f"/items/{k}/custom-fields/cost", json={"value": None})).status_code == 200
    assert "cost" not in (await api.call("GET", f"/items/{k}/custom-fields")).json()["data"]


async def test_required_field_cannot_be_cleared_and_definitions_are_admin_only(api: Api) -> None:
    k = await item(api)
    f = (
        await api.call(
            "POST",
            "/custom-fields",
            json={"name": "R", "key": "req", "field_type": "text", "is_required": True},
        )
    ).json()
    assert (await api.call("PUT", f"/items/{k}/custom-fields/req", json={"value": None})).status_code == 400
    assert (
        await api.call(
            "POST", "/custom-fields", as_="@sara", json={"name": "X", "key": "x", "field_type": "text"}
        )
    ).status_code in (403, 404)
    assert (await api.call("PATCH", f"/custom-fields/{f['id']}", json={"name": "Required"})).json()[
        "name"
    ] == "Required"
    assert (await api.call("DELETE", f"/custom-fields/{f['id']}")).status_code == 204
    assert (await api.call("PUT", f"/items/{k}/custom-fields/req", json={"value": "x"})).status_code == 404


async def test_project_level_field_shadows_workspace_field(api: Api) -> None:
    await api.call(
        "POST",
        "/custom-fields",
        json={"name": "Sev", "key": "sev", "field_type": "select", "options": ["low", "high"]},
    )
    await api.call(
        "POST",
        "/custom-fields",
        json={
            "name": "Sev",
            "key": "sev",
            "field_type": "select",
            "options": ["p1", "p2"],
            "project": "DATA",
        },
    )
    web, data = await item(api, project="WEB"), await item(api, project="DATA")
    assert (
        await api.call("PUT", f"/items/{web}/custom-fields/sev", json={"value": "high"})
    ).status_code == 200
    assert (
        await api.call("PUT", f"/items/{data}/custom-fields/sev", json={"value": "high"})
    ).status_code == 400
    assert (
        await api.call("PUT", f"/items/{data}/custom-fields/sev", json={"value": "p1"})
    ).status_code == 200


# ---------------------------------------------------------------- attachments
@pytest.fixture
def store(api: Api, tmp_path: Path) -> Path:
    api.c._transport.app.state.settings.attachment_dir = str(tmp_path)  # type: ignore[attr-defined]
    return tmp_path


async def test_upload_download_and_delete_round_trip(
    api: Api, store: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    k = await item(api)
    up = await raw(api, "POST", f"/items/{k}/attachments", params={"filename": "../../etc/pass wd.png"}, content=b"\x89PNG-bytes", content_type="image/png")  # fmt: skip
    assert up.status_code == 201, up.text
    a = up.json()
    assert "storage_url" not in a and a["filename"] == "pass wd.png" and a["size_bytes"] == 10
    assert any(
        p.name == a["id"] for p in blobs(store)
    )  # on disk under <ws>/<item>/<id>, never under the filename
    got = await api.call("GET", f"/attachments/{a['id']}/content")
    assert got.content == b"\x89PNG-bytes" and got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-type"] == "image/png" and got.headers["content-disposition"].startswith(
        "inline"
    )
    assert (await api.call("GET", f"/items/{k}/attachments")).json()["data"][0]["id"] == a["id"]
    assert (await api.call("DELETE", f"/attachments/{a['id']}")).status_code == 204
    assert not blobs(store)
    assert (await api.call("GET", f"/attachments/{a['id']}/content")).status_code == 404
    assert await events(session_factory, "attachment.added") == 1


async def test_active_content_is_forced_to_download_and_sandboxed(api: Api, store: Path) -> None:
    k = await item(api)
    a = (
        await raw(api, "POST", f"/items/{k}/attachments", params={"filename": "x.html"},
                  content=b"<script>1</script>", content_type="text/html")
    ).json()  # fmt: skip
    r = await api.call("GET", f"/attachments/{a['id']}/content")
    assert r.headers["content-type"] == "application/octet-stream" and r.headers[
        "content-disposition"
    ].startswith("attachment")
    assert "sandbox" in r.headers["content-security-policy"]


async def test_upload_limits_links_and_visibility(api: Api, store: Path) -> None:
    k = await item(api)
    api.c._transport.app.state.settings.attachment_max_bytes = 5  # type: ignore[attr-defined]
    assert (
        await raw(api, "POST", f"/items/{k}/attachments", params={"filename": "a"}, content=b"123456")
    ).status_code == 413
    assert (
        await raw(api, "POST", f"/items/{k}/attachments", params={"filename": "a"}, content=b"")
    ).status_code == 400
    api.c._transport.app.state.settings.attachment_max_bytes = 1000  # type: ignore[attr-defined]
    ln = await raw(
        api,
        "POST",
        f"/items/{k}/attachments",
        params={"filename": "l", "kind": "link"},
        content=b'{"url": "https://wiki.test/x", "title": "Wiki"}',
    )
    assert ln.status_code == 201 and ln.json()["filename"] == "Wiki"
    r = await api.call("GET", f"/attachments/{ln.json()['id']}/content")
    assert r.status_code == 307 and r.headers["location"] == "https://wiki.test/x"
    bad = await raw(
        api,
        "POST",
        f"/items/{k}/attachments",
        params={"filename": "l", "kind": "link"},
        content=b'{"url": "javascript:alert(1)"}',
    )
    assert bad.status_code == 400


async def test_local_storage_refuses_keys_outside_its_root(tmp_path: Path) -> None:
    s = LocalDiskStorage(tmp_path)
    url = await s.put("a/b", b"x")
    assert url == "local://a/b" and await s.get(url) == b"x"
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(StorageError):
            await s.put(bad, b"x")
    with pytest.raises(StorageError):
        await s.get("s3://bucket/key")
    await s.delete(url)
    with pytest.raises(StorageError):
        await s.get(url)


# ---------------------------------------------------------------- S3 adapter (pre-signed URLs)
def test_sigv4_matches_the_aws_documentation_example() -> None:
    """AWS docs, "Authenticating requests: using query parameters": GET examplebucket/test.txt, 2013-05-24, 86400 s."""
    from datetime import UTC, datetime

    from tt_server.storage.adapter import presign

    url = presign(
        "GET", host="examplebucket.s3.amazonaws.com", path="/test.txt", region="us-east-1",
        access_key="AKIAIOSFODNN7EXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", expires=86400,
        now=datetime(2013, 5, 24, 0, 0, 0, tzinfo=UTC),
    )  # fmt: skip
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")
    assert "X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2F20130524%2Fus-east-1%2Fs3%2Faws4_request" in url


async def test_s3_storage_round_trip_uses_signed_urls_and_forced_download() -> None:
    import httpx
    from tt_server.storage.adapter import S3Storage

    seen: list[httpx.Request] = []
    blobs: dict[str, bytes] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        key = req.url.path
        if req.method == "PUT":
            blobs[key] = req.content
            return httpx.Response(200)
        if req.method == "GET":
            return httpx.Response(200, content=blobs[key]) if key in blobs else httpx.Response(404)
        blobs.pop(key, None)
        return httpx.Response(204)

    s3 = S3Storage("tt", region="eu-west-1", access_key="AK", secret_key="SK", endpoint="http://minio:9000",
                   client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))  # fmt: skip
    url = await s3.put("ws/item/att1", b"hello")
    assert url == "s3://ws/item/att1" and await s3.get(url) == b"hello"
    assert all("X-Amz-Signature" in str(r.url) and "X-Amz-Expires=900" in str(r.url) for r in seen)
    link = s3.signed_download_url(
        url, disposition='attachment; filename="x.html"', content_type="application/octet-stream"
    )
    assert (
        "response-content-disposition=attachment" in link
        and "X-Amz-Expires=120" in link
        and link.startswith("http://minio:9000/tt/ws/item/att1?")
    )
    await s3.delete(url)
    with pytest.raises(StorageError):
        await s3.get(url)
    with pytest.raises(StorageError):
        await s3.put("../escape", b"x")
