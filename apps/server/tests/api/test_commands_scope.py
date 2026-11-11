"""Phase 14 review M4/M5: `classify` and `risk.action.add` authorise against the object's own project, not the workspace."""

from __future__ import annotations

from tests.api.conftest import Api
from tests.api.test_commands import code, one

ROUTERS_COVERED = ("commands",)


async def _scoped_manager(api: Api) -> None:
    """@sara is a member of WEB and DATA (so she can see both) but is a manager of WEB only."""
    for p in ("WEB", "DATA"):
        await api.call("POST", f"/projects/{p}/members", json={"handle": "@sara"})
    await api.role("@sara", "manager", scope_type="project", scope_id="WEB")


async def _theme(api: Api) -> None:
    r = await api.call("POST", "/dimensions", json={"key": "theme", "name": "Theme", "applies_to": ["work_item", "project"]})  # fmt: skip
    assert r.status_code == 201, r.text
    assert (await one(api, "dimension.value.add", {"dimension": "theme", "name": "Checkout"}))[
        "status"
    ] == "applied"


async def test_classify_a_project_target_is_authorised_on_that_project(api: Api) -> None:
    await _scoped_manager(api)
    await _theme(api)
    args = {"dimension": "theme", "value": "checkout", "target_type": "project"}
    ok = await one(api, "classify", {**args, "target": "WEB"}, as_="@sara")
    assert ok["status"] == "applied", ok  # was: not_found (the executor looked the project key up as an item)
    assert code(await one(api, "classify", {**args, "target": "DATA"}, as_="@sara")) == "forbidden"


async def test_classify_an_item_in_another_project_is_forbidden_for_a_project_scoped_manager(
    api: Api,
) -> None:
    await _scoped_manager(api)
    await _theme(api)
    web = (await one(api, "item.create", {"title": "w", "project": "WEB"}))["result"]["key"]
    data = (await one(api, "item.create", {"title": "d", "project": "DATA"}))["result"]["key"]
    args = {"dimension": "theme", "value": "checkout"}
    assert (await one(api, "classify", {**args, "target": web}, as_="@sara"))["status"] == "applied"
    assert code(await one(api, "classify", {**args, "target": data}, as_="@sara")) == "forbidden"


async def test_classify_a_missing_target_is_not_found_not_a_crash(api: Api) -> None:
    await _theme(api)
    res = await one(api, "classify", {"dimension": "theme", "value": "checkout", "target": "NOPE", "target_type": "project"})  # fmt: skip
    assert code(res) == "not_found"


async def test_risk_action_add_follows_the_risks_project(api: Api) -> None:
    await _scoped_manager(api)
    web = (await one(api, "risk.create", {"project": "WEB", "title": "web risk"}))["result"]
    data = (await one(api, "risk.create", {"project": "DATA", "title": "data risk"}))["result"]
    ok = await one(
        api, "risk.action.add", {"risk": web["key"], "description": "call the vendor"}, as_="@sara"
    )
    assert ok["status"] == "applied", ok  # was: forbidden (the executor demanded workspace-wide risk.write)
    denied = await one(api, "risk.action.add", {"risk": data["key"], "description": "x"}, as_="@sara")
    assert code(denied) == "forbidden"


async def test_risk_link_follows_the_risks_project(api: Api) -> None:
    await _scoped_manager(api)
    web_risk = (await one(api, "risk.create", {"project": "WEB", "title": "wr"}))["result"]
    data_risk = (await one(api, "risk.create", {"project": "DATA", "title": "dr"}))["result"]
    web_item = (await one(api, "item.create", {"title": "wi", "project": "WEB"}))["result"]["key"]
    assert (await one(api, "risk.link", {"risk": web_risk["key"], "item": web_item}, as_="@sara"))["status"] == "applied"  # fmt: skip
    assert code(await one(api, "risk.link", {"risk": data_risk["key"], "item": web_item}, as_="@sara")) == "forbidden"  # fmt: skip
