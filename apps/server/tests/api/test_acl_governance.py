"""Phase 8: policy authoring, evaluation, violation lifecycle, notifications, auto-actions, habits and metrics (router: governance)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.base import new_id
from tt_schema.enums import (
    EventSource,
    PolicyCategory,
    Severity,
    TargetType,
    ViolationStatus,
)
from tt_server.db import models as m

from tests.api.conftest import Api

ROUTERS_COVERED = ("governance", "governance_habits", "governance_violations")

NO_EST = {
    "template": "no_estimate_weighted",
    "params": {"min_weight": 0},
    "trigger": "scheduled",
    "schedule": "0 6 * * *",
    "event_types": None,
}


async def pid(api: Api, handle: str) -> str:
    return {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}[handle]  # type: ignore[no-any-return]


async def active_item(
    api: Api,
    title: str = "work",
    project: str = "WEB",
    as_: str = "@mahmoud",
    assignee: str | None = None,
    **extra: Any,
) -> str:
    if assignee:
        extra["assignee_id"] = await pid(api, assignee)
    r0 = await api.call("POST", "/items", as_=as_, json={"title": title, "project": project, **extra})
    assert r0.status_code == 201, r0.text
    key = r0.json()["key"]
    r = await api.call("POST", f"/items/{key}/state", as_=as_, json={"state": "doing"})
    assert r.status_code == 200, r.text
    return key  # type: ignore[no-any-return]


async def make_policy(api: Api, key: str = "no_est", activate: bool = True, **body: Any) -> dict[str, Any]:
    r = await api.call("POST", "/policies", json={"key": key, "name": key, **{**NO_EST, **body}})
    assert r.status_code == 201, r.text
    p = r.json()
    if activate:
        assert (await api.call("POST", f"/policies/{key}:test")).status_code == 200
        a = await api.call("POST", f"/policies/{key}:activate")
        assert a.status_code == 200, a.text
    return p  # type: ignore[no-any-return]


async def estimate(api: Api, key: str, hours: int | None) -> None:
    if hours is not None:
        r = await api.send(action="estimate", item={"key": key}, estimate={"value": hours, "unit": "hours"})
        assert r["status"] in ("applied", "ok", "created", "matched", "updated") or r, r


async def set_state(api: Api, key: str, state: str) -> None:
    assert (await api.call("POST", f"/items/{key}/state", json={"state": state})).status_code == 200


async def patch_item(api: Api, key: str, **fields: Any) -> None:
    tag = (await api.call("GET", f"/items/{key}")).headers["etag"]
    r = await api.c.request(
        "PATCH", api.url(f"/items/{key}"), headers={"X-Debug-Actor": "@mahmoud", "If-Match": tag}, json=fields
    )
    assert r.status_code == 200, r.text


async def run(api: Api, key: str, at: datetime | None = None) -> dict[str, Any]:
    r = await api.call("POST", f"/policies/{key}:run", params={"at": at.isoformat()} if at else None)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def violations(api: Api, as_: str = "@mahmoud", **params: Any) -> list[dict[str, Any]]:
    r = await api.call("GET", "/violations", as_=as_, params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]  # type: ignore[no-any-return]


# ---------------------------------------------------------------- catalogue and context
async def test_the_catalogue_ships_at_least_ten_templates_and_the_context_schema(api: Api) -> None:
    t = (await api.call("GET", "/policy-templates")).json()["data"]
    assert len(t) >= 10 and {"stuck_in_stage", "wip_breach", "overwork", "evidence_gap"} <= {
        x["key"] for x in t
    }
    assert (
        all(x["is_builtin"] for x in t)
        and next(x for x in t if x["key"] == "overwork")["category"] == "wellbeing"
    )
    ctx = (await api.call("GET", "/meta/policy-context")).json()
    assert ctx["version"] == 1 and {"item", "activity", "person", "stage", "metrics"} <= set(ctx["roots"])
    assert all(f["doc"] for f in ctx["functions"]) and "days_in_stage" in {
        f["name"] for f in ctx["functions"]
    }
    assert (
        await api.call("GET", "/policy-templates", as_="@ali")
    ).status_code == 403  # contributors do not author policies
    assert len((await api.call("GET", "/habit-definitions")).json()["data"]) >= 11


# ---------------------------------------------------------------- authoring
async def test_a_typo_in_raw_cel_is_rejected_at_save_naming_the_field(api: Api) -> None:
    body = {
        "key": "typo",
        "name": "typo",
        "condition_cel": "item.wieght > 3",
        "target": "work_item",
        "trigger": "scheduled",
        "schedule": "0 * * * *",
    }
    r = await api.call("POST", "/policies", json=body)
    err = r.json()["error"]
    assert (
        r.status_code == 400
        and err["pointer"] == "/condition_cel"
        and err["details"]["field"] == "item.wieght"
        and "wieght" in err["message"]
    )
    ok = await api.call(
        "POST",
        "/policies",
        json={**body, "condition_cel": 'item.state.category == "active" && item.weight > 3'},
    )
    assert (
        ok.status_code == 201 and ok.json()["is_active"] is False and ok.json()["context_schema_version"] == 1
    )
    syntax = await api.call(
        "POST", "/policies", json={**body, "key": "syn", "condition_cel": "item.weight >"}
    )
    assert syntax.status_code == 400 and syntax.json()["error"]["details"]["line"] == 1


async def test_managers_use_templates_but_raw_cel_needs_policy_admin(api: Api) -> None:
    await api.role("@sara", "manager")
    raw = {
        "key": "raw",
        "name": "raw",
        "condition_cel": "item.weight > 1",
        "target": "work_item",
        "trigger": "scheduled",
        "schedule": "0 * * * *",
    }
    forbid = await api.call("POST", "/policies", as_="@sara", json=raw)
    assert forbid.status_code == 403 and forbid.json()["error"]["details"]["permission"] == "policy.admin"
    ok = await api.call("POST", "/policies", as_="@sara", json={"key": "mine", "name": "mine", **NO_EST})
    assert ok.status_code == 201
    assert (
        await api.call("POST", "/policies", as_="@ali", json={"key": "xx", "name": "x", **NO_EST})
    ).status_code == 403


async def test_template_parameters_are_checked_and_cannot_inject(api: Api) -> None:
    bad = await api.call(
        "POST",
        "/policies",
        json={"key": "b1", "name": "b", "template": "stuck_in_stage", "params": {"days": 0}},
    )
    assert bad.status_code == 400 and bad.json()["error"]["pointer"] == "/params/days"
    unk = await api.call(
        "POST",
        "/policies",
        json={"key": "b2", "name": "b", "template": "stuck_in_stage", "params": {"dayz": 3}},
    )
    assert unk.status_code == 400
    inj = await api.call(
        "POST",
        "/policies",
        json={
            "key": "b3",
            "name": "b",
            "template": "unclassified_in_stage",
            "params": {"dimension": "x || true"},
        },
    )
    assert inj.status_code == 400
    nope = await api.call(
        "POST", "/policies", json={"key": "b4", "name": "b", "template": "no_such_template"}
    )
    assert nope.status_code == 404
    bad_sched = await api.call(
        "POST", "/policies", json={"key": "b5", "name": "b", **{**NO_EST, "schedule": "nonsense"}}
    )
    assert bad_sched.status_code == 400 and bad_sched.json()["error"]["pointer"] == "/schedule"
    bad_filter = await api.call(
        "POST", "/policies", json={"key": "b6", "name": "b", **NO_EST, "candidate_filter": {"bogus": 1}}
    )
    assert bad_filter.status_code == 400 and bad_filter.json()["error"]["pointer"].startswith(
        "/candidate_filter"
    )
    dup = await api.call("POST", "/policies", json={"key": "dd", "name": "d", **NO_EST})
    assert dup.status_code == 201
    assert (await api.call("POST", "/policies", json={"key": "dd", "name": "d", **NO_EST})).status_code == 409


async def test_activation_needs_a_test_run_newer_than_the_last_edit(api: Api) -> None:
    await make_policy(api, activate=False)
    refused = await api.call("POST", "/policies/no_est:activate")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "test_required"
    await active_item(api)
    t = (await api.call("POST", "/policies/no_est:test", json={"window": "7d"})).json()
    assert t["fired_count"] == 1 and len(t["sample_target_ids"]) == 1 and t["evaluated"] == 1
    assert (await api.call("POST", "/policies/no_est:activate")).json()["is_active"] is True
    edited = await api.call("PATCH", "/policies/no_est", json={"params": {"min_weight": 5}})
    assert (
        edited.status_code == 200 and edited.json()["is_active"] is False
    )  # an edited rule must be re-tested
    assert (await api.call("POST", "/policies/no_est:activate")).status_code == 409
    forced = await api.call("POST", "/policies/no_est:activate", params={"force": True})
    assert forced.status_code == 200 and forced.json()["is_active"] is True
    bad = await api.call("POST", "/policies/no_est:test", json={"window": "forever"})
    assert bad.status_code == 422 or bad.status_code == 400


async def test_a_dry_run_writes_no_violations(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await active_item(api)
    await make_policy(api, activate=False)
    t = (await api.call("POST", "/policies/no_est:test")).json()
    assert t["fired_count"] == 1
    async with session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(m.Violation)) == 0
        assert await s.scalar(select(func.count()).select_from(m.PolicyTestRun)) == 1


# ---------------------------------------------------------------- evaluation
async def test_evaluating_twice_opens_one_violation_and_a_cleared_condition_auto_resolves(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    key = await active_item(api, "needs an estimate")
    await make_policy(api)
    first = await run(api, "no_est")
    again = await run(api, "no_est")
    assert first["opened"] == 1 and again["opened"] == 0 and again["matched"] == 1
    [v] = await violations(api, status="open")
    assert (
        v["policy"] == "no_est"
        and v["evidence"]["item"] == key
        and v["evidence"]["detail"].endswith("no estimate")
    )
    async with session_factory() as s:  # the database refuses a second open row, too
        row = await s.get(m.Violation, v["id"])
        assert row is not None
        s.add(
            m.Violation(
                id=new_id(),
                workspace_id=row.workspace_id,
                policy_id=row.policy_id,
                target_type=row.target_type,
                target_id=row.target_id,
                severity=row.severity,
                category=row.category,
                status=ViolationStatus.OPEN,
                detected_at=datetime.now(UTC),
                evidence={},
            )
        )
        try:
            await s.commit()
            raise AssertionError("the partial unique index did not fire")
        except Exception as exc:  # noqa: BLE001
            assert "uq_violations_open_target" in str(exc)
    await estimate(api, key, 3)
    cleared = await run(api, "no_est")
    assert cleared["auto_resolved"] == 1
    [done] = await violations(api, status="resolved")
    assert done["resolution_note"] == "condition cleared"
    types = {
        e["event_type"] for e in (await api.call("GET", "/events", params={"limit": 100})).json()["data"]
    }
    assert {"violation.opened", "violation.auto_resolved", "policy.created", "policy.activated"} <= types


async def test_grace_period_delays_the_first_violation(api: Api) -> None:
    await active_item(api)
    await make_policy(api, grace_period="2d")
    t0 = datetime(2026, 10, 1, 9, tzinfo=UTC)
    assert (await run(api, "no_est", t0))["pending_grace"] == 1
    assert await violations(api) == []
    mid = await run(api, "no_est", t0 + timedelta(days=1))
    assert mid["opened"] == 0
    late = await run(api, "no_est", t0 + timedelta(days=3))
    assert late["opened"] == 1 and len(await violations(api)) == 1


async def test_scope_and_candidate_filter_limit_what_is_evaluated(api: Api) -> None:
    web, data = await active_item(api, "w", "WEB"), await active_item(api, "d", "DATA")
    await make_policy(api, key="web_only", scope_type="project", scope_id="WEB")
    await make_policy(api, key="high_only", candidate_filter={"priority": {"gte": "high"}})
    await run(api, "web_only")
    assert {v["evidence"]["item"] for v in await violations(api, policy="web_only")} == {web}
    assert (await run(api, "high_only"))["matched"] == 0  # both items are `medium`
    await patch_item(api, data, priority="high")
    assert (await run(api, "high_only"))["matched"] == 1
    assert (
        await api.call(
            "POST",
            "/policies",
            json={"key": "bad_scope", "name": "x", **NO_EST, "scope_type": "project", "scope_id": "NOPE"},
        )
    ).status_code == 404


# ---------------------------------------------------------------- violation lifecycle
async def test_acknowledge_resolve_waive_and_false_positive_with_reasons(api: Api) -> None:
    await api.role("@sara", "manager")
    keys = [await active_item(api, f"i{n}") for n in range(3)]
    await make_policy(api)
    await run(api, "no_est")
    vs = {v["evidence"]["item"]: v for v in await violations(api)}
    assert set(vs) == set(keys)
    a, b, c = (vs[k]["id"] for k in keys)
    assert (await api.call("POST", f"/violations/{a}:acknowledge")).json()["status"] == "acknowledged"
    assert (await api.call("POST", f"/violations/{a}:acknowledge")).status_code == 409
    assert (await api.call("POST", f"/violations/{a}:resolve", json={"note": "fixed"})).json()[
        "resolution_note"
    ] == "fixed"
    assert (await api.call("POST", f"/violations/{a}:resolve", json={})).status_code == 409  # terminal
    assert (await api.call("POST", f"/violations/{b}:waive", json={"reason": "x"})).status_code == 400
    w = (
        await api.call(
            "POST", f"/violations/{b}:waive", as_="@sara", json={"reason": "spike, agreed with PM"}
        )
    ).json()
    assert w["status"] == "waived" and w["waived_by"] and w["waive_reason"] == "spike, agreed with PM"
    fp = (
        await api.call(
            "POST", f"/violations/{c}:false-positive", json={"reason": "this is research, not delivery"}
        )
    ).json()
    assert fp["status"] == "false_positive"
    assert (
        await api.call("POST", f"/violations/{c}:resolve", as_="@ali", json={})
    ).status_code == 404  # @ali cannot see it at all


async def test_a_waived_violation_stays_quiet_until_the_condition_has_cleared_once(api: Api) -> None:
    key = await active_item(api)
    await make_policy(api)
    await run(api, "no_est")
    [v] = await violations(api)
    assert (
        await api.call("POST", f"/violations/{v['id']}:waive", json={"reason": "known gap"})
    ).status_code == 200
    again = await run(api, "no_est")
    assert again["matched"] == 1 and again["opened"] == 0  # still true, still waived: no new violation
    await set_state(api, key, "done")
    await run(api, "no_est")  # the condition clears (the item is no longer active) ...
    await set_state(api, key, "doing")
    reopened = await run(api, "no_est")  # ... and comes back: a new violation
    assert reopened["opened"] == 1


async def test_visibility_follows_the_projects_and_wellbeing_follows_the_reporting_line(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await active_item(api, "x", "WEB")
    await make_policy(api)
    await run(api, "no_est")
    assert (
        len(await violations(api)) == 1 and await violations(api, as_="@ali") == []
    )  # @ali is not a member of WEB
    sara, mahmoud = await pid(api, "@sara"), await pid(api, "@mahmoud")
    assert (
        await api.call(
            "POST", "/reporting-lines", json={"person_id": sara, "manager_id": mahmoud, "kind": "line"}
        )
    ).status_code == 201
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        pol = await s.scalar(select(m.Policy).where(m.Policy.key == "no_est"))
        assert ws and pol
        s.add(
            m.Violation(
                id=new_id(),
                workspace_id=ws.id,
                policy_id=pol.id,
                target_type=TargetType.PERSON,
                target_id=sara,
                person_id=sara,
                severity=Severity.WARNING,
                category=PolicyCategory.WELLBEING,
                status=ViolationStatus.OPEN,
                detected_at=datetime.now(UTC),
                evidence={"detail": "640 min"},
            )
        )
        await s.commit()
    seen = {
        w: [v["category"] for v in await violations(api, as_=w, category="wellbeing")]
        for w in ("@sara", "@mahmoud", "@ali")
    }
    assert seen == {"@sara": ["wellbeing"], "@mahmoud": ["wellbeing"], "@ali": []}
    await api.role("@ali", "manager", scope_type="project", scope_id="WEB")
    assert (
        await violations(api, as_="@ali", category="wellbeing") == []
    )  # a project manager never sees wellbeing


# ---------------------------------------------------------------- notifications and actions
async def test_notifications_reach_the_subject_and_the_line_manager_once_per_day(api: Api) -> None:
    sara, mahmoud = await pid(api, "@sara"), await pid(api, "@mahmoud")
    await api.call(
        "POST", "/reporting-lines", json={"person_id": sara, "manager_id": mahmoud, "kind": "line"}
    )
    await api.call("POST", "/projects/WEB/members", json={"handle": "@sara"})
    key = await active_item(api, "mine", assignee="@sara")
    await make_policy(api, notify={"person": True, "reporting_line": ["line"], "roles": ["manager"]})
    t0 = datetime(2026, 10, 22, 9, tzinfo=UTC)
    first = await run(api, "no_est", t0)
    assert first["opened"] == 1 and first["notified"] == 2
    again = await run(api, "no_est", t0 + timedelta(hours=1))
    assert again["notified"] == 0  # the violation is already open: no second message
    mine = (await api.call("GET", "/notifications/mine", as_="@sara")).json()["data"]
    boss = (await api.call("GET", "/notifications/mine", as_="@mahmoud")).json()["data"]
    assert len(mine) == 1 and len(boss) == 1 and mine[0]["body"].endswith("no estimate") and key
    assert (await api.call("GET", "/notifications/mine", as_="@ali")).json()["data"] == []
    read = await api.call("POST", f"/notifications/{mine[0]['id']}:read", as_="@sara")
    assert read.json()["read_at"] is not None
    assert (await api.call("POST", f"/notifications/{boss[0]['id']}:read", as_="@sara")).status_code == 404
    assert (await api.call("GET", "/notifications/mine", as_="@sara", params={"unread": True})).json()[
        "data"
    ] == []


async def test_auto_actions_are_audited_as_rule_events(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    key = await active_item(api, "escalate me")
    bad = await api.call(
        "POST", "/policies", json={"key": "xx", "name": "x", **NO_EST, "auto_action": {"drop_table": 1}}
    )
    assert bad.status_code == 400 and bad.json()["error"]["pointer"] == "/auto_action"
    await make_policy(api, auto_action={"set": {"priority": "critical"}})
    out = await run(api, "no_est")
    assert out["opened"] == 1 and out["actions"] == 1
    item = (await api.call("GET", f"/items/{key}")).json()
    assert item["priority"] == "critical"
    async with session_factory() as s:
        evs = (await s.scalars(select(m.Event).where(m.Event.event_type == "policy.auto_action"))).all()
        assert (
            len(evs) == 1
            and evs[0].source == EventSource.RULE
            and evs[0].payload["after"]["action"] == {"set": {"priority": "critical"}}
        )
    await run(api, "no_est")
    assert (await api.call("GET", "/events", params={"limit": 200})).status_code == 200


# ---------------------------------------------------------------- templates shared between workspaces
async def test_a_shared_template_is_copied_not_linked(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    body = {
        "key": "slow_review",
        "name": "Slow review",
        "category": "process",
        "cel_source": 'item.state.category == "active" && item.days_in_stage() > {days}',
        "target": "work_item",
        "trigger": "scheduled",
        "schedule": "0 * * * *",
        "params": [{"name": "days", "type": "int", "default": 5, "min": 1, "max": 90}],
        "detail": "{item.days_in_stage()} days",
        "published_scope": "shared",
    }
    t = await api.call("POST", "/policy-templates", json=body)
    assert t.status_code == 201, t.text
    async with session_factory() as s:  # a second workspace
        other = m.Workspace(id=new_id(), name="Other", slug="other", settings={})
        s.add(other)
        await s.commit()
        other_id = other.id
    copy = await api.call(
        "POST", f"/policy-templates/{t.json()['id']}:copy"
    )  # same workspace: allowed (shared or own)
    assert copy.status_code == 201 and copy.json()["id"] != t.json()["id"]
    from tt_server.governance import service

    async with session_factory() as s:
        src = await s.get(m.PolicyTemplate, t.json()["id"])
        assert src
        c = await service.copy_template(s, src, other_id, actor="x")
        await s.commit()
        src.cel_source = "item.weight > 99"  # later edit of the source
        await s.commit()
        await s.refresh(c)
        assert (
            c.workspace_id == other_id
            and c.cel_source.endswith("> {days}")
            and c.published_scope == "workspace"
        )
    bad = await api.call(
        "POST", "/policy-templates", json={**body, "key": "broken", "cel_source": "item.nope > {days}"}
    )
    assert bad.status_code == 400 and bad.json()["error"]["pointer"] == "/cel_source"
    unshared = await api.call("POST", f"/policy-templates/{t.json()['id']}:publish", params={"shared": False})
    assert unshared.json()["published_scope"] == "workspace"


# ---------------------------------------------------------------- habits, metrics, key results, stage rules
async def test_habits_and_metric_snapshots_after_a_recompute(api: Api) -> None:
    await api.role("@sara", "manager")
    sara = await pid(api, "@sara")
    for n in range(3):
        await active_item(api, f"s{n}", as_="@mahmoud", assignee="@sara")
    await make_policy(api)
    out = (
        await api.call("POST", "/governance:recompute", params={"at": datetime.now(UTC).isoformat()})
    ).json()
    assert out["policies_swept"] >= 1 and out["snapshots"] > 0
    habits = (await api.call("GET", "/habits", params={"subject": "@sara", "key": "no_estimates"})).json()[
        "data"
    ]
    assert (
        habits
        and habits[0]["polarity"] == "negative"
        and habits[0]["occurrences"] == 3
        and habits[0]["subject_id"] == sara
    )
    assert {
        h["key"]
        for h in (await api.call("GET", "/habits", as_="@sara", params={"subject": "@sara"})).json()["data"]
    } >= {"no_estimates"}
    patch = await api.call(
        "PATCH",
        f"/habits/{habits[0]['id']}",
        as_="@sara",
        json={"status": "in_coaching", "coaching_note": "pair on estimates"},
    )
    assert patch.status_code == 200 and patch.json()["status"] == "in_coaching"
    await api.call("POST", "/governance:recompute", params={"at": datetime.now(UTC).isoformat()})
    again = (await api.call("GET", "/habits", params={"subject": "@sara", "key": "no_estimates"})).json()[
        "data"
    ]
    assert (
        again[0]["status"] == "in_coaching" and again[0]["coaching_note"] == "pair on estimates"
    )  # manager-owned fields survive
    assert (
        await api.call("PATCH", f"/habits/{habits[0]['id']}", as_="@ali", json={"status": "resolved"})
    ).status_code == 404
    snap = (
        await api.call("GET", "/metric-snapshots", params={"scope_type": "project", "scope_id": "WEB"})
    ).json()["data"]
    assert (
        len(snap) == 1
        and snap[0]["metrics"]["wip"] == 3
        and snap[0]["metrics"]["violation_count"]["planning"] == 3
    )
    person = (
        await api.call("GET", "/metric-snapshots", params={"scope_type": "person", "scope_id": "@sara"})
    ).json()["data"]
    assert person[0]["metrics"]["open_items"] == 3
    assert (
        await api.call(
            "GET", "/metric-snapshots", as_="@ali", params={"scope_type": "person", "scope_id": "@sara"}
        )
    ).status_code == 404


async def test_a_metric_key_result_follows_the_nightly_snapshot(api: Api) -> None:
    await active_item(api, "k1")
    obj = (
        await api.call(
            "POST",
            "/objectives",
            json={"scope_type": "workspace", "scope_id": "acme", "title": "Flow", "period": "2026-Q4"},
        )
    ).json()
    kr = (
        await api.call(
            "POST",
            f"/objectives/{obj['id']}/key-results",
            json={
                "title": "WIP",
                "target_value": 5,
                "unit": "items",
                "metric_key": "wip",
                "source": "metric_snapshot",
            },
        )
    ).json()
    assert Decimal(str(kr["current_value"])) == 0
    out = (
        await api.call("POST", "/governance:recompute", params={"at": datetime.now(UTC).isoformat()})
    ).json()
    assert out["key_results"] == 1
    krs = (await api.call("GET", f"/objectives/{obj['id']}/key-results")).json()["data"]
    assert Decimal(str(krs[0]["current_value"])) == 1


async def test_auto_when_moves_an_item_when_a_merged_pr_is_attached(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from tt_server.governance import runner

    key = await active_item(api, "ship it")
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws
        proc = (
            await s.scalars(
                select(m.Process).where(m.Process.workspace_id == ws.id, m.Process.is_default.is_(True))
            )
        ).one()
        stages = {
            st.key: st for st in (await s.scalars(select(m.Stage).where(m.Stage.process_id == proc.id))).all()
        }
        s.add(
            m.StageTransitionRule(
                id=new_id(),
                process_id=proc.id,
                from_stage_id=stages["build"].id,
                to_stage_id=stages["test"].id,
                allowed_roles=[],
                required_evidence_kinds=[],
                auto_when='event.type == "evidence.added" && event.payload.after.kind == "pr" && event.payload.after.title.contains("merged")',
            )
        )
        await s.commit()
    await api.call("PUT", f"/items/{key}/stage", json={"stage": "build"})
    await api.send(action="evidence", item={"key": key}, evidence=[{"kind": "pr", "title": "PR 12 opened"}])
    await api.send(action="evidence", item={"key": key}, evidence=[{"kind": "pr", "title": "PR 12 merged"}])
    async with session_factory() as s:
        evs = (
            await s.scalars(
                select(m.Event).where(m.Event.event_type == "evidence.added").order_by(m.Event.occurred_at)
            )
        ).all()
    assert len(evs) == 2
    moved = [await runner.on_event(session_factory, e.id) for e in evs]
    assert moved == [0, 1]
    placements = (await api.call("GET", f"/items/{key}/placements")).json()["data"]
    assert placements[-1]["stage"] == "test" or any(p.get("stage") == "test" for p in placements)
    async with session_factory() as s:
        ev = (
            await s.scalars(
                select(m.Event).where(m.Event.event_type == "stage.moved", m.Event.source == EventSource.RULE)
            )
        ).all()
        to_test = [e for e in ev if e.payload["after"]["stage"] == "test"]
        assert len(to_test) == 1 and to_test[0].correlation_id == evs[1].id


async def test_on_event_policies_react_to_the_event_they_listen_to(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from tt_server.governance import runner

    await make_policy(
        api,
        key="on_ev",
        template="no_estimate_weighted",
        params={"min_weight": 0},
        trigger="on_event",
        schedule=None,
        event_types=["item.state_changed"],
    )
    key = await active_item(api, "evented")
    async with session_factory() as s:
        evs = (
            await s.scalars(
                select(m.Event)
                .where(m.Event.event_type == "item.state_changed", m.Event.entity_id.is_not(None))
                .order_by(m.Event.occurred_at)
            )
        ).all()
        target = [e for e in evs if (e.payload or {}).get("after", {}).get("state") == "doing"]
    assert target
    assert await runner.on_event(session_factory, target[-1].id) == 1
    assert [v["evidence"]["item"] for v in await violations(api, policy="on_ev")] == [key]
    # an event type nobody listens to evaluates nothing
    async with session_factory() as s:
        other = (await s.scalars(select(m.Event).where(m.Event.event_type == "item.created"))).first()
    assert other and await runner.on_event(session_factory, other.id) == 0
    await estimate(api, key, 4)
    assert (
        await runner.on_event(session_factory, target[-1].id) == 1
    )  # re-evaluating the same entity clears its violation
    assert (await violations(api, policy="on_ev", status="resolved"))[0][
        "resolution_note"
    ] == "condition cleared"


async def test_a_deleted_policy_stops_everything(api: Api) -> None:
    await active_item(api)
    await make_policy(api)
    assert (await api.call("DELETE", "/policies/no_est")).status_code == 204
    assert (await api.call("GET", "/policies/no_est")).status_code == 404
    assert (await api.call("GET", "/policies")).json()["data"] == []
    assert (await api.call("POST", "/policies/no_est:run")).status_code == 404
