"""Phase 9 thin editors, server side: policy version history (restorable), habit sparkline series, and the request-hardening net
found by the OpenAPI contract run (NUL bytes, bad enums/limits, dangling references answer 4xx, never 500)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tests.api.conftest import Api
from tests.api.test_acl_governance import active_item, make_policy, pid

ROUTERS_COVERED = ("governance",)


async def test_policy_versions_record_who_changed_what_and_restore_by_patch(api: Api) -> None:
    await make_policy(api, "v_pol", activate=False)
    await api.call("PATCH", "/policies/v_pol", json={"severity": "critical", "notify": {"person": True}})
    await api.call(
        "PATCH", "/policies/v_pol", json={"scope_type": "project", "scope_id": "WEB", "grace_period": "2d"}
    )
    vs = (await api.call("GET", "/policies/v_pol/versions")).json()["data"]
    assert [v["kind"] for v in vs] == ["updated", "updated", "created"]
    assert (
        set(vs[0]["changed"]) >= {"scope_type", "scope_id", "grace_period"}
        and "severity" not in vs[0]["changed"]
    )
    assert vs[1]["definition"]["severity"] == "critical" and vs[2]["definition"]["severity"] != "critical"
    # restore the first edit's definition: the server accepts exactly what the editor sends back
    d = vs[1]["definition"]
    body: dict[str, Any] = {
        k: d[k]
        for k in ("name", "severity", "scope_type", "scope_id", "notify", "auto_action", "grace_period")
    }
    assert (await api.call("PATCH", "/policies/v_pol", json=body)).status_code == 200
    now = (await api.call("GET", "/policies/v_pol")).json()
    assert now["severity"] == "critical" and now["grace_period"] is None and now["scope_type"] == "workspace"
    assert (
        len((await api.call("GET", "/policies/v_pol/versions")).json()["data"]) == 4
    )  # history is append-only


async def test_policy_versions_need_policy_read_and_unknown_policy_is_404(api: Api) -> None:
    await make_policy(api, "v2", activate=False)
    assert (await api.call("GET", "/policies/nope/versions")).status_code == 404
    assert (await api.call("GET", "/policies/v2/versions", as_="@ali")).status_code in (403, 404)


async def test_habit_series_matches_the_stored_habit_and_respects_visibility(api: Api) -> None:
    await api.role("@sara", "manager")
    for n in range(3):
        await active_item(api, f"s{n}", as_="@mahmoud", assignee="@sara")
    await make_policy(api)
    await api.call("POST", "/governance:recompute", params={"at": datetime.now(UTC).isoformat()})
    h = (await api.call("GET", "/habits", params={"subject": "@sara", "key": "no_estimates"})).json()["data"][
        0
    ]
    got = (await api.call("GET", f"/habits/{h['id']}/series", params={"points": 6})).json()["data"]
    assert len(got) == 6 and got[0]["at"] < got[-1]["at"]
    last = got[-1]
    assert (
        last["rate"] is not None
        and abs(last["rate"] - float(h["rate"])) < 0.01
        and last["occurrences"] == h["occurrences"]
    )
    assert (await api.call("GET", f"/habits/{h['id']}/series", params={"points": 1})).status_code == 400
    assert (await api.call("GET", "/habits/missing/series")).status_code == 404
    other = await pid(api, "@ali")
    assert other  # @ali is not a manager of @sara: the series is as hidden as the habit
    assert (await api.call("GET", f"/habits/{h['id']}/series", as_="@ali")).status_code in (403, 404)


async def test_hostile_inputs_answer_4xx_not_500(api: Api) -> None:
    c = api.c
    hdr = {"X-Debug-Actor": "@mahmoud"}
    for path, params in (
        ("/items", {"state_category": "banana"}),
        ("/items", {"limit": -5}),
        ("/violations", {"limit": 99999999999}),
        ("/habits", {"window": "x" * 20}),
        ("/habits", {"status": "nope"}),
        ("/insights", {"status": "nope"}),
        ("/milestones", {"upcoming": "55191515956406490d"}),
        ("/context-notes", {"scope_id": "a\x00b"}),
        ("/events", {"limit": 0}),
    ):
        r = await c.get(api.url(path), params=params, headers=hdr)
        assert 400 <= r.status_code < 500, (path, params, r.status_code, r.text[:120])
    r = await c.post(
        api.url("/objectives"),
        headers=hdr,
        json={"title": "t", "owner_id": "nobody", "scope_type": "team", "scope_id": "zz", "period": "Q1"},
    )
    assert 400 <= r.status_code < 500, r.text
    r = await c.post(
        api.url("/products"),
        headers={**hdr, "content-type": "application/json"},
        content=b'{"key": "P\\u0000X", "name": "n"}',
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_parameter"
    dup = {"name": "Dupe", "key": "DUPE"}
    assert (await c.post(api.url("/products"), headers=hdr, json=dup)).status_code in (200, 201)
    again = await c.post(api.url("/products"), headers=hdr, json=dup)
    assert again.status_code in (400, 409), again.text
