"""Permission and visibility behaviour of the overlay router (Phase 4; router: overlay)."""

from __future__ import annotations

from tests.api.conftest import Api
from tests.api.test_acl_core import pid

ROUTERS_COVERED = ("overlay",)


async def _item(api: Api, project: str = "WEB", **extra: object) -> dict[str, str]:
    r = await api.call("POST", "/items", json={"title": "overlay target", "project": project, **extra})
    assert r.status_code == 201, r.text
    return dict(r.json())


async def test_feature_admin_roles_gate_dimensions_process_and_products(api: Api) -> None:
    dim = {"key": "region", "name": "Region"}
    prod = {"key": "ACME", "name": "Acme Suite"}
    assert (await api.call("POST", "/dimensions", as_="@ali", json=dim)).status_code == 403
    assert (await api.call("POST", "/products", as_="@ali", json=prod)).status_code == 403
    assert (
        await api.call("POST", "/processes", as_="@ali", json={"name": "P", "key": "p"})
    ).status_code == 403
    await api.role("@ali", "feature_admin:dimensions")
    await api.role("@ali", "feature_admin:products")
    assert (await api.call("POST", "/dimensions", as_="@ali", json=dim)).status_code == 201
    assert (await api.call("POST", "/products", as_="@ali", json=prod)).status_code == 201
    assert (
        await api.call("POST", "/processes", as_="@ali", json={"name": "P", "key": "p"})
    ).status_code == 403


async def test_overlay_writes_need_item_write_overlay_and_invisible_items_are_404(api: Api) -> None:
    item = await _item(api)
    body = {"priority": "high"}
    # sara is not on WEB at all: the item does not exist for her
    assert (await api.call("PATCH", f"/items/{item['key']}", as_="@sara", json=body)).status_code == 404
    assert (
        await api.call("PUT", f"/items/{item['key']}/stage", as_="@sara", json={"stage": "x"})
    ).status_code == 404
    # as a plain WEB member she can see it but not change the overlay
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    r = await api.call("PUT", f"/items/{item['key']}/weights/anything", as_="@sara", json={"value": "3"})
    assert r.status_code == 403 and r.json()["error"]["details"]["permission"] == "weight.set"
    assert (await api.call("GET", f"/items/{item['key']}/provenance", as_="@sara")).status_code == 200
    assert (await api.call("GET", f"/items/{item['key']}/provenance", as_="@ali")).status_code == 404


async def test_risks_follow_project_visibility_and_risk_write(api: Api) -> None:
    risk = {"project": "WEB", "title": "vendor slips"}
    assert (await api.call("POST", "/risks", as_="@ali", json=risk)).status_code in (403, 404)
    created = await api.call("POST", "/risks", json=risk)
    assert created.status_code == 201
    titles = lambda r: {x["title"] for x in r.json()["data"]}  # noqa: E731
    assert titles(await api.call("GET", "/risks")) == {"vendor slips"}
    assert titles(await api.call("GET", "/risks", as_="@ali")) == set()  # not on WEB
    await api.call("POST", "/projects/WEB/members", json={"handle": "@ali"})
    assert titles(await api.call("GET", "/risks", as_="@ali")) == {"vendor slips"}
    patch = await api.call("PATCH", f"/risks/{created.json()['id']}", as_="@ali", json={"probability": 5})
    assert patch.status_code == 403  # member can read, may not edit


async def test_classification_confirmation_is_for_the_line_manager(api: Api) -> None:
    sara = await pid(api, "@sara")
    r = await api.call(
        "POST",
        "/reporting-lines",
        json={"person_id": sara, "manager_id": await pid(api, "@ali"), "kind": "line"},
    )
    assert r.status_code == 201
    await api.send(
        as_="@sara", action="create", item={"title": "needs a product"}, project="nowhere", product="ACME"
    )
    assert (await api.call("GET", "/classifications", as_="@ali")).status_code == 200
    outsider = await api.call("GET", "/classifications", as_="@mahmoud")
    assert outsider.status_code == 200  # admin sees the workspace overlay, but never Sara's inbox items
    keys = {c.get("target_key") for c in outsider.json()["data"]}
    assert not any(k and str(k).startswith("INBOX-sara") for k in keys)


async def test_approvals_and_review_queue_are_gated(api: Api) -> None:
    assert (await api.call("GET", "/review-queue", as_="@ali")).status_code == 403  # nobody's manager
    assert (await api.call("GET", "/review-queue")).status_code == 200
    assert (await api.call("GET", "/approvals", as_="@ali")).status_code == 200
