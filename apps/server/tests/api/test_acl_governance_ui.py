"""Phase 9 API: editor check, weight preview, manager digest, coaching-note visibility (router: governance_ui)."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.api.conftest import Api
from tests.api.test_acl_governance import active_item, make_policy, pid

ROUTERS_COVERED = ("governance_ui",)


async def test_context_schema_alias_and_version(api: Api) -> None:
    r = await api.call("GET", "/governance/context-schema", params={"version": 1})
    assert r.status_code == 200 and "item" in r.json()["roots"] and r.json()["functions"]
    assert (await api.call("GET", "/governance/context-schema", params={"version": 2})).status_code == 404


async def test_check_reports_errors_in_the_body_with_the_field(api: Api) -> None:
    ok = (await api.call("POST", "/policies:check", json={"cel": "item.weight > 1"})).json()
    assert ok["ok"] is True and ok["errors"] == []
    bad = await api.call("POST", "/policies:check", json={"cel": "item.priority > 3"})
    assert bad.status_code == 200 and bad.json()["ok"] is False and bad.json()["errors"][0]["message"]
    unk = (await api.call("POST", "/policies:check", json={"cel": "item.nope == 1"})).json()
    assert unk["ok"] is False and "nope" in unk["errors"][0]["message"]
    t = (
        await api.call(
            "POST", "/policies:check", json={"template": "no_estimate_weighted", "params": {"min_weight": 0}}
        )
    ).json()
    assert t["ok"] is True and "estimate" in t["cel"]
    badp = (
        await api.call(
            "POST",
            "/policies:check",
            json={"template": "no_estimate_weighted", "params": {"min_weight": "x"}},
        )
    ).json()
    assert badp["ok"] is False


async def test_raw_cel_check_needs_policy_admin(api: Api) -> None:
    await api.role("@sara", "manager")
    r = await api.call("POST", "/policies:check", as_="@sara", json={"cel": "item.weight > 1"})
    assert r.status_code == 403
    t = await api.call(
        "POST", "/policies:check", as_="@sara", json={"template": "no_estimate_weighted", "params": {}}
    )
    assert t.status_code == 200
    assert (await api.call("POST", "/policies:check", as_="@ali", json={"cel": "true"})).status_code == 403


async def test_weight_preview_equals_the_server_recomputation(api: Api) -> None:
    for n in range(3):
        await active_item(api, f"w{n}", priority="high")
    formula = {"base": "constant", "constant": 2, "multipliers": {"priority.high": 3}, "clamp": [0, 100]}
    r = await api.call("POST", "/weight-schemes:preview", json={"formula": formula, "sample": 20})
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert len(rows) >= 3 and all(x["preview"] == 6.0 for x in rows if x["title"].startswith("w"))
    assert r.json()["total_preview"] == round(sum(x["preview"] for x in rows), 4)
    bad = await api.call("POST", "/weight-schemes:preview", json={"formula": {"base": "bogus"}})
    assert bad.status_code == 400
    assert (
        await api.call("POST", "/weight-schemes:preview", as_="@ali", json={"formula": formula})
    ).status_code == 403


async def test_digest_lists_logs_events_and_needs_review_permission(api: Api) -> None:
    await active_item(api, "digested")
    r = await api.call("GET", "/digest", params={"days": 14})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["days"] == 14 and {"daily_logs", "events", "notes", "insights", "unreviewed"} <= set(d)
    assert (await api.call("GET", "/digest", as_="@ali")).status_code == 403
    assert (await api.call("GET", "/digest", params={"team": "nope"})).status_code == 404


async def test_coaching_notes_never_reach_the_subject(api: Api) -> None:
    await api.role("@sara", "manager")
    sara = await pid(api, "@sara")
    for n in range(3):
        await active_item(api, f"c{n}", assignee="@sara")
    await make_policy(api)
    await api.call("POST", "/governance:recompute", params={"at": datetime.now(UTC).isoformat()})
    h = (await api.call("GET", "/habits", params={"subject": "@sara", "key": "no_estimates"})).json()["data"][
        0
    ]
    assert h["subject_id"] == sara
    p = await api.call(
        "PATCH", f"/habits/{h['id']}", json={"status": "in_coaching", "coaching_note": "pair up"}
    )
    assert p.json()["coaching_note"] == "pair up"
    mine = (await api.call("GET", "/habits", as_="@sara", params={"key": "no_estimates"})).json()["data"]
    assert mine and all("coaching_note" not in x for x in mine) and mine[0]["status"] == "in_coaching"
    seen = (await api.call("GET", "/habits", params={"subject": "@sara", "key": "no_estimates"})).json()[
        "data"
    ]
    assert seen[0]["coaching_note"] == "pair up"


async def test_describe_the_rule_returns_a_draft_the_server_validates(api: Api) -> None:
    from tests.api.test_acl_presentations import _gw

    api.c._transport.app.state.llm = None  # type: ignore[attr-defined]
    assert (
        await api.call("POST", "/policies:author", json={"request": "flag items stuck"})
    ).status_code == 409
    seen = await _gw(
        api,
        {
            "mode": "template",
            "template": "no_estimate_weighted",
            "params": {"min_weight": 0},
            "validated": True,
        },
    )
    r = await api.call("POST", "/policies:author", json={"request": "items without estimates"})
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["validated"] is True and seen[0]["request"] == "items without estimates"
    await _gw(
        api, {"mode": "cel", "cel": "item.nope == 1", "validated": True}
    )  # the model claims it; the server checks
    bad = (await api.call("POST", "/policies:author", json={"request": "something odd"})).json()
    assert bad["draft"]["validated"] is False and bad["errors"]
    api.c._transport.app.state.llm = None  # type: ignore[attr-defined]


async def test_a_malformed_notify_shape_is_rejected_when_saved(api: Api) -> None:
    r = await api.call(
        "POST",
        "/policies",
        json={
            "key": "badnotify",
            "name": "x",
            "template": "no_estimate_weighted",
            "notify": {"reporting_line": True},
        },
    )
    assert r.status_code == 400 and r.json()["error"]["pointer"] == "/notify/reporting_line"
    r2 = await api.call(
        "POST",
        "/policies",
        json={
            "key": "badnotify",
            "name": "x",
            "template": "no_estimate_weighted",
            "notify": {"reporting_line": ["boss"]},
        },
    )
    assert r2.status_code == 400
    ok = await api.call(
        "POST",
        "/policies",
        json={
            "key": "oknotify",
            "name": "x",
            "template": "no_estimate_weighted",
            "notify": {"reporting_line": ["line"]},
        },
    )
    assert ok.status_code == 201
