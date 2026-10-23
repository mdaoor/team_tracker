"""Every review-queue action performs its write (UX §3.2) and closes the row."""

from __future__ import annotations

from tests.api.conftest import Api


async def queue(api: Api, as_: str = "@mahmoud") -> list[dict]:  # type: ignore[type-arg]
    return (await api.call("GET", "/review-queue", as_=as_)).json()["data"]


async def resolve(api: Api, ref: str, choice: dict, as_: str = "@mahmoud"):  # type: ignore[no-untyped-def,type-arg]
    return await api.call("POST", "/review-queue/resolve", as_=as_, json={"ref": ref, "choice": choice})


async def test_unknown_person_stage_product_risk_tag_actions(api: Api) -> None:
    await api.call("POST", "/products", json={"key": "SHOP", "name": "Shop"})
    risk = (
        await api.call(
            "POST",
            "/risks",
            json={"project": "WEB", "title": "r", "target_type": "work_item", "target_id": None},
        )
    ).status_code
    assert risk == 400  # an item-scoped risk needs a target
    r = (await api.call("POST", "/risks", json={"project": "WEB", "title": "Provider risk"})).json()
    res = await api.send(
        action="create",
        item={"title": "needs review"},
        project="WEB",
        tags=["fresh"],
        assignees=[{"person": "@ghost", "role": "owner"}],
        stage="nonexistent",
        product="NOPE",
        risks=[r["key"], "X-9"],
        priority="high",
        llm_confidence={"priority": 0.3},
    )
    key = res["item"]["key"]
    rows = {x["kind"]: x for x in await queue(api) if x["item_key"] == key}
    assert {
        "unknown_person",
        "stage_suggestion",
        "product_suggestion",
        "risk_mention",
        "provisional_tag",
        "low_confidence_match",
    } <= set(rows)
    # unknown person -> @sara
    assert (await resolve(api, rows["unknown_person"]["ref"], {"handle": "@sara"})).json()[
        "resolved"
    ] == "assigned @sara"
    d = (await api.call("GET", f"/items/{key}")).json()
    assert d["assignee_id"]
    # stage -> design (placed by a manager: confirmed)
    assert (await resolve(api, rows["stage_suggestion"]["ref"], {"stage": "design"})).status_code == 200
    assert (await api.call("GET", f"/items/{key}")).json()["stage"] == "design"
    # product -> SHOP
    assert (await resolve(api, rows["product_suggestion"]["ref"], {"product": "SHOP"})).json()[
        "resolved"
    ] == "product SHOP"
    # risk -> the real one
    assert (await resolve(api, rows["risk_mention"]["ref"], {"risk": r["key"]})).json()[
        "resolved"
    ] == f"linked {r['key']}"
    # low confidence priority applied by a human
    assert (await resolve(api, rows["low_confidence_match"]["ref"], {"apply": True})).json()[
        "resolved"
    ] == "applied priority"
    assert (await api.call("GET", f"/items/{key}")).json()["priority"] == "high"
    # provisional tag confirmed
    assert (
        "confirmed"
        in (await resolve(api, rows["provisional_tag"]["ref"], {"confirm": True})).json()["resolved"]
    )
    left = [x for x in await queue(api) if x["item_key"] == key]
    assert left == []
    again = await resolve(api, rows["unknown_person"]["ref"], {"handle": "@sara"})
    assert again.status_code == 409


async def test_merge_provisional_tag_and_ignore_and_bulk(api: Api) -> None:
    await api.send(action="create", item={"title": "tagged one"}, project="WEB", tags=["alpha"])
    second = await api.send(action="create", item={"title": "tagged two"}, project="WEB", tags=["beta"])
    tags = {t["name"]: t["id"] for t in (await api.call("GET", "/tags")).json()["data"]}
    rows = [x for x in await queue(api) if x["kind"] == "provisional_tag"]
    alpha = next(x for x in rows if x["suggestion"]["name"] == "alpha")
    out = await resolve(api, alpha["ref"], {"merge_into": tags["beta"]})
    assert out.json()["resolved"] == "merged into beta"
    assert {t["name"] for t in (await api.call("GET", "/tags")).json()["data"]} == {"beta"}
    beta = next(x for x in await queue(api) if x["kind"] == "provisional_tag")
    ign = await resolve(api, beta["ref"], {"ignore": True})
    assert ign.json()["resolved"] == "ignored"
    await api.send(action="create", item={"title": "x"}, project="nowhere", tags=["gamma"])
    refs = [x["ref"] for x in await queue(api)]
    bulk = (
        await api.call(
            "POST", "/review-queue/bulk", json={"refs": refs + ["review_item:nope"], "action": "ignore"}
        )
    ).json()
    assert bulk["resolved"] == len(refs) and bulk["failed"][0]["error"] == "not_found"
    assert await queue(api) == []
    assert second["item"]["key"]
    bad = await resolve(api, "garbage", {})
    assert bad.status_code == 400


async def test_missing_estimate_and_classification_reject(api: Api) -> None:
    res = await api.send(action="create", item={"title": "estimate me"}, project="WEB", stage="build")
    key = res["item"]["key"]
    cl = next(x for x in await queue(api) if x["kind"] == "unconfirmed_classification")
    rej = await resolve(api, cl["ref"], {"action": "reject"})
    assert rej.json()["resolved"] == "reject"
    assert (await api.call("GET", "/classifications", params={"target": key, "confirmed": "false"})).json()[
        "data"
    ] == []
