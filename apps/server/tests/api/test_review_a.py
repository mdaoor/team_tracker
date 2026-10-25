"""Review 2026-10-24, package A: security fixes with regression tests (findings 1-7)."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import pytest
from tt_server.api.routers import presentations as pres_router

from tests.api.conftest import Api


@pytest.fixture
def bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b = tmp_path / "bundle.html"
    b.write_text("<!doctype html><html><head><title>d</title></head><body></body></html>")
    monkeypatch.setattr(pres_router, "BUNDLE", b)


# ---------------------------------------------------------------- A1
@pytest.mark.parametrize(
    "seg", ["x%22;alert(1);%2F%2F", "a%3Cscript%3E", 'a"b', "a<b", "..%2F..", "a b", "a;b"]
)
async def test_deck_shell_rejects_hostile_segments(api: Api, bundle: None, seg: str) -> None:
    for url in (f"/p/t/{seg}", f"/p/acme/{seg}", f"/p/{seg}/abc"):
        r = await api.c.get(url)
        assert r.status_code in (404, 307, 308, 422), (
            url,
            r.status_code,
        )  # never a page carrying the segment
        assert "alert" not in r.text and "__DECK_SRC__" not in r.text


async def test_deck_shell_encodes_the_source_as_a_json_literal(api: Api, bundle: None) -> None:
    r = await api.c.get("/p/t/pt_abc-123_XYZ")
    assert r.status_code == 200
    assert 'window.__DECK_SRC__="/api/v1/public/p/pt_abc-123_XYZ/data";' in r.text
    r2 = await api.c.get("/p/acme/0192-abc")
    assert 'window.__DECK_SRC__="/api/v1/w/acme/presentations/0192-abc/data";' in r2.text
    assert '<script nonce="' in r2.text and ">window.__DECK_SRC__" in r2.text and r2.text.count("</script>") == 1


# ---------------------------------------------------------------- A2
async def mint(api: Api, scopes: list[str], bearer: str | None = None) -> Any:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {"X-Debug-Actor": "@mahmoud"}
    return await api.c.post("/auth/tokens", headers=headers, json={"workspace": "acme", "scopes": scopes})


async def test_a_token_cannot_mint_a_wider_token(api: Api) -> None:
    cap = (await mint(api, ["capture"])).json()["token"]
    for wider in (["read"], ["manage"], ["admin"], ["capture", "admin"]):
        r = await mint(api, wider, bearer=cap)
        assert r.status_code == 403 and r.json()["error"]["code"] == "scope_ceiling", wider
    ok = await mint(api, ["capture"], bearer=cap)
    assert ok.status_code == 201
    mgr = (await mint(api, ["capture", "read", "manage"])).json()["token"]
    assert (await mint(api, ["read"], bearer=mgr)).status_code == 201
    assert (await mint(api, ["admin"], bearer=mgr)).status_code == 403
    assert (await mint(api, ["admin"])).status_code == 201  # a session / debug actor may mint any


# ---------------------------------------------------------------- A3
async def test_review_resolve_needs_ownership_or_permission_on_the_item(api: Api) -> None:
    await api.send(as_="@mahmoud", action="create", item={"title": "mine alone"}, project="the billing thing")
    rows = (await api.call("GET", "/review-queue")).json()["data"]
    ref = [r for r in rows if r["kind"] == "unclassified_project"][-1]["ref"]
    people = {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}
    # ali becomes *a* line manager (so the router gate passes) but of someone else
    await api.call(
        "POST",
        "/reporting-lines",
        json={"person_id": people["@sara"], "manager_id": people["@ali"], "is_primary": True},
    )
    bad = await api.call(
        "POST", "/review-queue/resolve", as_="@ali", json={"ref": ref, "choice": {"ignore": True}}
    )
    assert bad.status_code == 404
    bulk = await api.call("POST", "/review-queue/bulk", as_="@ali", json={"refs": [ref], "action": "ignore"})
    assert bulk.status_code == 200 and bulk.json()["resolved"] == 0 and bulk.json()["failed"][0]["ref"] == ref
    still = (await api.call("GET", "/review-queue")).json()["data"]
    assert ref in [r["ref"] for r in still]  # untouched
    ok = await api.call("POST", "/review-queue/resolve", json={"ref": ref, "choice": {"ignore": True}})
    assert ok.status_code == 200


# ---------------------------------------------------------------- A4
async def test_role_assignments_are_workspace_scoped(api: Api) -> None:
    r = await api.c.post(
        "/api/v1/workspaces",
        json={
            "name": "Beta",
            "slug": "beta",
            "owner": {"display_name": "Bea", "email": "bea@beta.test", "handle": "@bea"},
        },
    )
    assert r.status_code == 201, r.text
    beta = Api(api.c, "beta")
    theirs = [a for a in (await beta.call("GET", "/role-assignments", as_="@bea")).json()["data"]]
    assert theirs, "beta has its own owner assignment"
    foreign = theirs[0]["id"]
    assert foreign not in {a["id"] for a in (await api.call("GET", "/role-assignments")).json()["data"]}
    assert (await api.call("DELETE", f"/role-assignments/{foreign}")).status_code == 404
    still = (await beta.call("GET", "/role-assignments", as_="@bea")).json()["data"]
    assert foreign in {a["id"] for a in still}  # not revoked
    mine = (await api.call("GET", "/role-assignments")).json()["data"]
    assert all(a["workspace_id"] == next(x for x in mine)["workspace_id"] for a in mine)


# ---------------------------------------------------------------- A6
@pytest.mark.parametrize("fmt", ["pptx"])
async def test_pptx_with_empty_data_renders(api: Api, fmt: str) -> None:
    p = (await api.call("POST", "/presentations:from-template", json={"template": "portfolio_review"})).json()
    r = await api.call("POST", f"/presentations/{p['id']}/render", params={"format": fmt})
    assert r.status_code == 200 and r.content[:2] == b"PK"  # a zero-item workspace used to be a 500
    # a viewer who sees nothing gets the same: the deck of the owner rendered by someone restricted
    r2 = await api.call("POST", f"/presentations/{p['id']}/render", as_="@ali", params={"format": fmt})
    assert r2.status_code in (200, 404)


# ---------------------------------------------------------------- A7
async def test_layouts_and_csv_cover_more_than_one_page(api: Api) -> None:
    n = 230
    for i in range(n):
        r = await api.call("POST", "/items", json={"title": f"bulk {i}", "project": "WEB"})
        assert r.status_code == 201
    board = (await api.call("GET", "/projects/WEB/board")).json()
    assert sum(c["count"] for c in board["columns"]) == n == board["total"]
    todo = next(c for c in board["columns"] if c["state"] == "todo")
    assert todo["count"] == n and len(todo["items"]) == n
    out = await api.call("POST", "/query/items:csv", json={"filter": {"project": {"in": ["WEB"]}}})
    rows = list(csv.reader(io.StringIO(out.text)))
    assert len([r for r in rows if r and r[0].startswith("WEB-")]) == n  # every item, not one page
