"""Phase 3 acceptance criteria, run against the seeded `acme` workspace (each test rolls back)."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_schema.enums import EventSource, TargetType
from tt_server.db import models as m

from tests.api.conftest import Api

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.seed.demo import load_snapshot  # noqa: E402

SF = async_sessionmaker[AsyncSession]


async def events_of(sf: SF, key: str, *types: str) -> list[m.Event]:
    async with sf() as s:
        item = await s.scalar(select(m.WorkItem).where(m.WorkItem.key == key))
        assert item is not None
        stmt = select(m.Event).where(m.Event.entity_id == item.id)
        if types:
            stmt = stmt.where(m.Event.event_type.in_(types))
        return list((await s.scalars(stmt.order_by(m.Event.received_at, m.Event.id))).all())


async def test_stage_state_sync_both_directions_without_a_loop(sapi: Api, seed_sf: SF) -> None:
    snap = load_snapshot()
    # an item that the snapshot already has in production -> done; it has no earlier placement
    prod_key = next(i["key"] for i in snap["items"] if i["stage"] == "production")
    d = (await sapi.call("GET", f"/items/{prod_key}")).json()
    assert d["stage"] == "production" and d["state_category"] == "done"
    before = len(await events_of(seed_sf, prod_key))
    r = await sapi.call("POST", f"/items/{prod_key}/state", json={"state": "doing"})
    assert r.status_code == 200
    d = (await sapi.call("GET", f"/items/{prod_key}")).json()
    assert d["state_category"] == "active" and d["closed_at"] is None
    assert d["stage"] == "build"  # no earlier history: first execution stage
    moved = [
        e for e in await events_of(seed_sf, prod_key, "stage.moved") if e.source == EventSource.PROPAGATION
    ]
    assert len(moved) == 1 and moved[0].payload["reason"] == "state reopened"
    assert len(await events_of(seed_sf, prod_key)) - before <= 4  # bounded: no ping-pong

    # stage -> state: moving an open/active item to production completes it
    open_key = next(
        i["key"] for i in snap["items"] if i["stage"] in ("build", "test") and i["state"] == "active"
    )
    r = await sapi.call("PUT", f"/items/{open_key}/stage", json={"stage": "production"})
    assert r.status_code == 200
    d = (await sapi.call("GET", f"/items/{open_key}")).json()
    assert d["state_category"] == "done" and d["closed_at"] is not None and d["stage"] == "production"
    evs = await events_of(seed_sf, open_key, "stage.moved", "item.state_changed")
    assert [(e.event_type, e.source.value) for e in evs[-2:]] == [
        ("stage.moved", "overlay"),
        ("item.state_changed", "propagation"),
    ]
    assert (
        evs[-1].correlation_id
        and evs[-1].correlation_id == evs[-2].correlation_id
        or evs[-1].correlation_id == evs[-2].id
    )
    # and back: setting the state to doing returns to the last non-done stage it occupied
    await sapi.call("POST", f"/items/{open_key}/state", json={"state": "doing"})
    d = (await sapi.call("GET", f"/items/{open_key}")).json()
    assert d["stage"] in ("build", "test") and d["state_category"] == "active"


async def test_state_done_moves_stage_to_the_closed_stage(sapi: Api) -> None:
    snap = load_snapshot()
    key = next(i["key"] for i in snap["items"] if i["stage"] in ("design", "build") and i["state"] != "done")
    await sapi.call("POST", f"/items/{key}/state", json={"state": "done"})
    assert (await sapi.call("GET", f"/items/{key}")).json()["stage"] == "retired"


async def test_placement_history_and_board(sapi: Api) -> None:
    snap = load_snapshot()
    it = next(i for i in snap["items"] if i["stage"] == "build")
    await sapi.call("PUT", f"/items/{it['key']}/stage", json={"stage": "test", "reason": "ready"})
    hist = (await sapi.call("GET", f"/items/{it['key']}/placements")).json()["data"]
    assert [h["stage"] for h in hist] == ["build", "test"] and hist[0]["exited_at"] == hist[1]["entered_at"]
    board = (await sapi.call("GET", "/processes/sw-delivery/board")).json()
    cols = {c["stage"]: c for c in board["columns"]}
    assert list(cols) == ["idea", "discovery", "design", "build", "test", "staging", "production", "retired"]
    assert sum(len(c["items"]) for c in cols.values()) == 42
    assert any(card["key"] == it["key"] for card in cols["test"]["items"])
    assert cols["build"]["expected_duration_days"] == 10 and cols["build"]["wip"] == len(
        [c for c in cols["build"]["items"]]
    )


async def test_risk_links_inherit_down_and_unlink_keeps_explicit(sapi: Api, seed_sf: SF) -> None:
    snap = load_snapshot()
    children = [i["key"] for i in snap["items"] if i["epic"] == "DATA-80"]
    assert len(children) >= 2
    manual = children[0]

    async def linked() -> dict[str, bool]:
        async with seed_sf() as s:
            rows = (
                await s.execute(
                    select(m.WorkItem.key, m.RiskLink.inherited)
                    .join(m.RiskLink, m.RiskLink.work_item_id == m.WorkItem.id)
                    .join(m.Risk, m.Risk.id == m.RiskLink.risk_id)
                    .where(m.Risk.key == "DATA-R3", m.RiskLink.deleted_at.is_(None))
                )
            ).all()
        return dict(rows)

    # an explicit link on one child first (UX §7.2: a manual link survives the epic's unlink)
    assert (await sapi.call("POST", "/risks/DATA-R3/links", json={"item": manual})).status_code == 201
    before = await linked()
    assert before[manual] is False
    r = await sapi.call("POST", "/risks/DATA-R3/links", json={"item": "DATA-80"})
    assert r.status_code == 201
    after = await linked()
    assert set(children) | {"DATA-80"} <= set(after)
    assert after["DATA-80"] is False and after[manual] is False
    assert all(after[c] is True for c in children if c != manual and c not in before)
    un = await sapi.call("DELETE", "/risks/DATA-R3/links/DATA-80")
    assert un.status_code == 200 and un.json()["inherited_links_removed"] >= 1
    final = await linked()
    assert "DATA-80" not in final and manual in final and final[manual] is False
    assert {k for k in final} == set(before)  # exactly the links that existed before the epic was linked


async def test_priority_propagates_except_manual_and_is_idempotent(sapi: Api, seed_sf: SF) -> None:
    snap = load_snapshot()
    gov = [i["key"] for i in snap["items"] if i["project"] == "GOV"]
    manual_key = gov[0]
    d = (await sapi.call("GET", f"/items/{manual_key}")).json()
    r = (
        await sapi.call(
            "PATCH", f"/items/{manual_key}", json={"priority": "low"}, headers={"If-Match": d_etag(d)}
        )
        if False
        else None
    )
    etag = (await sapi.call("GET", f"/items/{manual_key}")).headers["etag"]
    r = await sapi.c.patch(
        sapi.url(f"/items/{manual_key}"),
        headers={"X-Debug-Actor": "@mahmoud", "If-Match": etag},
        json={"priority": "low"},
    )
    assert r.status_code == 200 and r.json()["priority"] == "low" and r.json()["priority_source"] == "manual"
    res = await sapi.call("PUT", "/projects/GOV/priority", json={"priority": "critical"})
    assert res.status_code == 200
    body = res.json()
    corr = body["correlation_id"]
    assert body["items_changed"] >= len(gov) - 1 and body["overrides_kept"] == 1
    rows = (await sapi.call("GET", "/items", params={"project": "GOV", "limit": 200})).json()["data"]
    prios = {r["key"]: r["priority"] for r in rows}
    assert prios[manual_key] == "low"
    assert all(v == "critical" for k, v in prios.items() if k != manual_key)
    async with seed_sf() as s:
        evs = (
            await s.scalars(
                select(m.Event).where(
                    m.Event.event_type == "item.priority_changed", m.Event.correlation_id == corr
                )
            )
        ).all()
        assert len(evs) >= len(gov) - 1 and {e.source for e in evs} == {EventSource.PROPAGATION}
        n_events = await s.scalar(select(func.count()).select_from(m.Event))
    again = await sapi.call("PUT", "/projects/GOV/priority", json={"priority": "critical"})
    assert again.json()["items_changed"] == 0
    async with seed_sf() as s:
        assert (
            await s.scalar(select(func.count()).select_from(m.Event)) - n_events <= 1
        )  # only the project-level event


def d_etag(d: dict) -> str:  # type: ignore[type-arg]
    return f'"{d["updated_at"]}"'


async def test_weight_schemes_per_scheme_rows_and_manual_override(sapi: Api, seed_sf: SF) -> None:
    created = await sapi.call(
        "POST", "/weight-schemes", json={"name": "effort-only", "formula": {"base": "estimate_hours"}}
    )
    assert created.status_code == 201
    assert (await sapi.call("POST", f"/weight-schemes/{created.json()['id']}/recompute")).json()[
        "recomputed"
    ] == 49
    snap = load_snapshot()
    with_est = next(i for i in snap["items"] if i["estimate_h"] == 8)
    w = (await sapi.call("GET", f"/items/{with_est['key']}/weights")).json()
    by_scheme = {r["scheme"]: Decimal(r["value"]) for r in w["data"]}
    assert by_scheme["effort-only"] == Decimal("8") and by_scheme["default"] == Decimal(with_est["weight"])
    assert Decimal(w["item_weight"]) == Decimal(
        with_est["weight"]
    )  # WorkItem.weight mirrors the default scheme only
    no_est = next(i for i in snap["items"] if i["estimate_h"] is None)
    w2 = {
        r["scheme"]: Decimal(r["value"])
        for r in (await sapi.call("GET", f"/items/{no_est['key']}/weights")).json()["data"]
    }
    assert w2["effort-only"] == Decimal("1")  # a missing estimate gives base 1.0
    # manual override survives recompute; value_score uses impact x weight / hours
    ov = await sapi.call("PUT", f"/items/{with_est['key']}/weights/effort-only", json={"value": "5"})
    assert ov.status_code == 200 and ov.json()["source"] == "manual"
    await sapi.call("POST", f"/weight-schemes/{created.json()['id']}/recompute")
    w3 = {
        r["scheme"]: r for r in (await sapi.call("GET", f"/items/{with_est['key']}/weights")).json()["data"]
    }
    assert Decimal(w3["effort-only"]["value"]) == 5 and w3["effort-only"]["source"] == "manual"
    bad = await sapi.call("POST", "/weight-schemes", json={"name": "bad", "formula": {"base": "magic"}})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_formula"
    async with seed_sf() as s:
        assert (
            await s.scalar(select(func.count()).select_from(m.Weight).where(m.Weight.source == "manual")) == 1
        )


async def test_value_score(sapi: Api) -> None:
    snap = load_snapshot()
    it = next(i for i in snap["items"] if i["estimate_h"] == 8)
    etag = (await sapi.call("GET", f"/items/{it['key']}")).headers["etag"]
    r = await sapi.c.patch(
        sapi.url(f"/items/{it['key']}"),
        headers={"X-Debug-Actor": "@mahmoud", "If-Match": etag},
        json={"impact": 4},
    )
    assert r.status_code == 200
    w = (await sapi.call("GET", f"/items/{it['key']}/weights")).json()
    assert Decimal(w["value_score"]) == (Decimal(4) * Decimal(it["weight"]) / Decimal(8)).quantize(
        Decimal("0.0001")
    )


async def test_approval_on_stage_entry_pending_then_rejected_rolls_back(sapi: Api, seed_sf: SF) -> None:
    snap = load_snapshot()
    key = next(i["key"] for i in snap["items"] if i["project"] == "GOV" and i["stage"] == "build")
    async with seed_sf() as s:
        staging = await s.scalar(select(m.Stage).where(m.Stage.key == "staging"))
        staging.requires_approval = True
        await s.commit()
    r = await sapi.call("PUT", f"/items/{key}/stage", json={"stage": "staging"})
    assert r.status_code == 200 and r.json()["approval_id"]
    board = (await sapi.call("GET", "/processes/sw-delivery/board")).json()
    card = next(
        c for col in board["columns"] if col["stage"] == "staging" for c in col["items"] if c["key"] == key
    )
    assert card["approval"] == "pending"
    pending = (await sapi.call("GET", "/approvals", params={"status": "pending", "mine": "true"})).json()[
        "data"
    ]
    assert len(pending) == 1
    nope = await sapi.call(
        "POST", f"/approvals/{pending[0]['id']}/decisions", as_="@lina", json={"decision": "approved"}
    )
    assert nope.status_code == 403
    rej = await sapi.call(
        "POST",
        f"/approvals/{pending[0]['id']}/decisions",
        json={"decision": "rejected", "comment": "not ready"},
    )
    assert rej.status_code == 201 and rej.json()["status"] == "rejected"
    hist = (await sapi.call("GET", f"/items/{key}/placements")).json()["data"]
    assert [h["stage"] for h in hist] == ["build", "staging", "build"] and hist[-1][
        "reason"
    ] == "approval rejected"
    again = await sapi.call("POST", f"/approvals/{pending[0]['id']}/decisions", json={"decision": "approved"})
    assert again.status_code == 409


async def test_review_queue_one_owner_per_row_and_resolution_learns_the_glossary(sapi: Api) -> None:
    # @ali (squad-b) types something about an unknown project; @lina (platform) hints a stage on a GOV item
    ali = await sapi.send(
        as_="@ali", action="create", item={"title": "tune the ingestion retry"}, project="the ingestion retry"
    )
    assert ali["item"]["project_key"] == "INBOX-ali"
    lina = await sapi.send(
        as_="@lina",
        action="start",
        item={"title": "gov rule editor polish"},
        project="GOV",
        stage="test",
        llm_confidence={"stage": 0.9},
    )
    mahmoud = (await sapi.call("GET", "/review-queue")).json()["data"]
    sara = (await sapi.call("GET", "/review-queue", as_="@sara")).json()["data"]
    assert {r["item_key"] for r in sara} == {ali["item"]["key"]} and sara[0]["kind"] == "unclassified_project"
    assert ali["item"]["key"] not in {r["item_key"] for r in mahmoud}
    kinds = {(r["item_key"], r["kind"]) for r in mahmoud}
    assert (lina["item"]["key"], "unconfirmed_classification") in kinds
    assert {r["item_key"] for r in mahmoud} == {lina["item"]["key"]}  # nothing of squad-b's
    # sara resolves the project: the item moves to DATA, gets a new key and the phrase is learned
    res = await sapi.call(
        "POST",
        "/review-queue/resolve",
        as_="@sara",
        json={"ref": sara[0]["ref"], "choice": {"project": "DATA"}},
    )
    assert res.status_code == 200, res.text
    moved = (await sapi.call("GET", f"/items/{ali['item']['key']}")).status_code
    assert moved == 404  # the old key is gone...
    items_ = (
        await sapi.call("GET", "/items", params={"project": "DATA", "q": "ingestion retry", "limit": 50})
    ).json()["data"]
    row = next(r for r in items_ if r["title"] == "tune the ingestion retry")
    assert row["key"].startswith("DATA-") and row["external_refs"]["previous_key"] == ali["item"]["key"]
    pack = (await sapi.call("GET", "/context-pack", as_="@sara")).json()
    assert pack["glossary"]["the ingestion retry"] == "PROJECT:DATA"
    assert (await sapi.call("GET", "/review-queue", as_="@sara")).json()["data"] == []
    # the next line with that phrase is routed without any review
    again = await sapi.send(
        as_="@ali", action="create", item={"title": "another retry thing"}, project="the ingestion retry"
    )
    assert again["item"]["project_key"] == "DATA" and again["resolution"]["project"]["method"] == "glossary"
    # unconfirmed classification: mahmoud confirms all suggestions
    done = await sapi.call("POST", "/review-queue/confirm-all-suggested")
    assert done.json()["confirmed"] >= 1
    assert (await sapi.call("GET", "/review-queue")).json()["data"] == []


async def test_capture_pipeline_completion_in_one_correlation(sapi: Api, seed_sf: SF) -> None:
    res = await sapi.send(
        as_="@nour", action="create", item={"title": "brand new pipeline check"}, project="DATA"
    )
    key = res["item"]["key"]
    d = (await sapi.call("GET", f"/items/{key}")).json()
    assert d["stage"] == "discovery" and d["project_key"] == "DATA"
    prov = (await sapi.call("GET", f"/items/{key}/provenance")).json()
    assert prov["product"][0]["value"] == "DPLAT" and prov["product"][0]["source"] == "inherited"
    assert prov["stage"][0]["value"] == "discovery"
    w = (await sapi.call("GET", f"/items/{key}/weights")).json()
    assert [r["scheme"] for r in w["data"]] == ["default"] and Decimal(w["item_weight"]) == 1
    evs = await events_of(seed_sf, key)
    assert {"item.created", "stage.moved"} <= {e.event_type for e in evs}
    assert len({e.correlation_id for e in evs}) == 1


async def test_hints_for_stage_product_and_risk_are_applied_unconfirmed(sapi: Api) -> None:
    res = await sapi.send(
        as_="@nour",
        action="create",
        item={"title": "hinted pipeline work"},
        project="DATA",
        stage="design",
        product="CUST",
        risks=["DATA-R3", "NOPE-1"],
    )
    key = res["item"]["key"]
    d = (await sapi.call("GET", f"/items/{key}")).json()
    assert d["stage"] == "design"
    unconf = (
        await sapi.call("GET", "/classifications", params={"target": key, "confirmed": "false"})
    ).json()["data"]
    assert {(c["dimension"], c["value"]) for c in unconf} == {("stage", "design"), ("product", "CUST")}
    queue = (await sapi.call("GET", "/review-queue")).json()["data"]
    assert {r["kind"] for r in queue if r["item_key"] == key} >= {
        "unconfirmed_classification",
        "risk_mention",
    }
    risk_row = next(r for r in queue if r["kind"] == "risk_mention")
    assert risk_row["suggestion"]["risks"] == [
        "NOPE-1"
    ]  # DATA-R3 resolved, only the unknown one is left to a manager


async def test_bulk_preview_equals_apply(sapi: Api) -> None:
    body = {"ids": ["GOV-10"], "set": {"priority": "high"}}
    pre = (await sapi.call("POST", "/items/bulk-preview", json=body)).json()
    snap = load_snapshot()
    assert pre["affected"] == 1 and pre["children_affected"] >= 1 and len(pre["sample"]) == 1
    applied = (await sapi.call("POST", "/items/bulk", json=body)).json()
    assert (applied["affected"], applied["children_affected"], applied["overrides_kept"]) == (
        pre["affected"],
        pre["children_affected"],
        pre["overrides_kept"],
    )
    rows = (await sapi.call("GET", "/items", params={"project": "GOV", "limit": 200})).json()["data"]
    assert all(r["priority"] == "high" for r in rows) and len(rows) == 1 + len(
        [i for i in snap["items"] if i["project"] == "GOV"]
    )
    bad = await sapi.call("POST", "/items/bulk-preview", json={"ids": ["GOV-10"], "set": {"title": "x"}})
    assert bad.status_code == 400
    stage_pre = await sapi.call(
        "POST",
        "/items/bulk",
        json={"filter": {"project": "GOV", "state_category": ["active"]}, "set": {"impact": 3}},
    )
    assert stage_pre.status_code == 200 and stage_pre.json()["affected"] >= 1


async def test_optimistic_concurrency_and_conflict_event(sapi: Api, seed_sf: SF) -> None:
    key = "WEB-101"
    first = await sapi.call("GET", f"/items/{key}")
    etag = first.headers["etag"]
    h = {"X-Debug-Actor": "@mahmoud"}
    none = await sapi.c.patch(sapi.url(f"/items/{key}"), headers=h, json={"description": "x"})
    assert none.status_code == 428
    ok = await sapi.c.patch(
        sapi.url(f"/items/{key}"), headers={**h, "If-Match": etag}, json={"description": "from manager A"}
    )
    assert ok.status_code == 200 and ok.headers["etag"] != etag
    stale = await sapi.c.patch(
        sapi.url(f"/items/{key}"),
        headers={**h, "X-Debug-Actor": "@lina", "If-Match": etag},
        json={"description": "from manager B"},
    )
    assert stale.status_code == 409
    err = stale.json()["error"]
    assert err["code"] == "conflict" and err["details"]["current"]["description"] == "from manager A"
    assert (
        err["details"]["changed_by"] == "@mahmoud" and err["details"]["changed_at"]
    )  # who and when (UX review "also")
    assert [e.event_type for e in await events_of(seed_sf, key, "item.field_conflict")] == [
        "item.field_conflict"
    ]
    retry = await sapi.c.patch(
        sapi.url(f"/items/{key}"),
        headers={**h, "X-Debug-Actor": "@lina", "If-Match": err["details"]["etag"]},
        json={"description": "from manager B"},
    )
    assert retry.status_code == 200 and retry.json()["description"] == "from manager B"


async def test_rollup_and_provenance(sapi: Api) -> None:
    ru = (await sapi.call("GET", "/items/WEB-120/rollup")).json()
    snap = load_snapshot()
    n = len([i for i in snap["items"] if i["epic"] == "WEB-120"])
    assert ru["item_count"] == n + 1 and sum(ru["stage_distribution"].values()) == n
    assert ru["hours_logged"] > 0 and Decimal(ru["sum_weight"]) >= n and ru["open_risk_score"] >= 0
    pv = (await sapi.call("GET", "/items/WEB-101/provenance")).json()
    assert {"project", "team", "priority", "stage", "type"} <= set(pv)
    assert pv["project"][0]["source"] == "rule" and pv["priority_source"] == "rule"


async def test_custom_dimension_lifecycle_and_classified_as(sapi: Api, seed_sf: SF) -> None:
    d = await sapi.call(
        "POST",
        "/dimensions",
        json={"key": "client", "name": "Client", "cardinality": "multi", "value_type": "tree"},
    )
    assert d.status_code == 201
    assert (await sapi.call("POST", "/dimensions", json={"key": "client", "name": "dup"})).status_code == 409
    assert (await sapi.call("POST", "/dimensions", json={"key": "Bad Key", "name": "x"})).status_code == 400
    assert (
        await sapi.call("POST", "/dimensions/client/values:bulk", json=["Acme Corp", "Globex"])
    ).json() == {"created": 2}
    vals = (await sapi.call("GET", "/dimensions/client/values")).json()["data"]
    acme = next(v for v in vals if v["key"] == "acme-corp")
    child = (
        await sapi.call(
            "POST",
            "/dimensions/client/values",
            json={"key": "acme-eu", "name": "Acme EU", "parent_value_id": acme["id"]},
        )
    ).json()
    cyc = await sapi.call(
        "PATCH", f"/dimensions/client/values/{acme['id']}", json={"parent_value_id": child["id"]}
    )
    assert cyc.status_code == 400 and cyc.json()["error"]["code"] == "cycle"
    for v in ("acme-corp", "globex"):  # multi cardinality: both stick
        assert (
            await sapi.call(
                "POST",
                "/classifications",
                json={"target_type": "work_item", "target_id": "WEB-101", "dimension": "client", "value": v},
            )
        ).status_code == 201
    cl = (
        await sapi.call("GET", "/classifications", params={"target": "WEB-101", "dimension": "client"})
    ).json()["data"]
    assert {c["value"] for c in cl} == {"acme-corp", "globex"}
    bad = await sapi.call(
        "POST",
        "/classifications",
        json={"target_type": "person", "target_id": "x", "dimension": "client", "value": "globex"},
    )
    assert bad.status_code == 400
    from tt_server.domain.overlay.classify import classified_as

    async with seed_sf() as s:
        ids = set((await s.scalars(classified_as("client", ["globex"]))).all())
        item = await s.scalar(select(m.WorkItem).where(m.WorkItem.key == "WEB-101"))
        assert item is not None and ids == {item.id}
        # classified_as also serves the builtin dimensions: it equals the real column
        gov = set((await s.scalars(classified_as("project", ["GOV"]))).all())
        real = set(
            (
                await s.scalars(
                    select(m.WorkItem.id)
                    .join(m.Project, m.Project.id == m.WorkItem.project_id)
                    .where(m.Project.key == "GOV")
                )
            ).all()
        )
        assert gov == real


async def test_single_cardinality_replaces_and_manual_wins_over_inherited(sapi: Api, seed_sf: SF) -> None:
    from tt_server.db.uow import UnitOfWork
    from tt_server.domain.overlay.products import inherit_product_for_project

    r = await sapi.call(
        "POST",
        "/classifications",
        json={"target_type": "work_item", "target_id": "WEB-105", "dimension": "product", "value": "DPLAT"},
    )
    assert r.status_code == 201
    cl = (
        await sapi.call("GET", "/classifications", params={"target": "WEB-105", "dimension": "product"})
    ).json()["data"]
    assert [c["value"] for c in cl] == ["DPLAT"] and cl[0][
        "source"
    ] == "manual"  # replaced the inherited CUST
    async with UnitOfWork(seed_sf) as uow:
        ws = await uow.session.scalar(select(m.Workspace))
        project = await uow.session.scalar(select(m.Project).where(m.Project.key == "WEB"))
        product = await uow.session.scalar(select(m.Product).where(m.Product.key == "CUST"))
        assert ws and project and product
        await inherit_product_for_project(uow, ws, project.id, product, actor_id=project.owner_id)
        await uow.session.flush()
    cl = (
        await sapi.call("GET", "/classifications", params={"target": "WEB-105", "dimension": "product"})
    ).json()["data"]
    assert [c["value"] for c in cl] == ["DPLAT"]  # propagation did not overwrite the manual choice


async def test_products_tree_cycle_and_single_delivering_product(sapi: Api) -> None:
    child = await sapi.call(
        "POST", "/products", json={"key": "PORTAL", "name": "Portal", "parent_product_id": None}
    )
    assert child.status_code == 201
    pid = child.json()["id"]
    sub = await sapi.call("POST", "/products", json={"key": "SUB", "name": "Sub", "parent_product_id": pid})
    assert sub.status_code == 201
    cyc = await sapi.call("PATCH", "/products/PORTAL", json={"parent_product_id": sub.json()["id"]})
    assert cyc.status_code == 400 and cyc.json()["error"]["code"] == "cycle"
    dup = await sapi.call(
        "POST",
        "/products/PORTAL/links",
        json={"target_type": "project", "target_id": "WEB", "role": "delivers"},
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "delivers_exists"
    ok = await sapi.call(
        "POST",
        "/products/PORTAL/links",
        json={"target_type": "project", "target_id": "WEB", "role": "supports"},
    )
    assert ok.status_code == 201
    assert (
        await sapi.call(
            "POST",
            "/products/PORTAL/links",
            json={"target_type": "project", "target_id": "WEB", "role": "supports"},
        )
    ).status_code == 409


async def test_objectives_are_multi_valued_classifications(sapi: Api) -> None:
    o1 = (
        await sapi.call(
            "POST",
            "/objectives",
            json={"scope_type": "workspace", "scope_id": "acme", "title": "Grow", "period": "2026-Q4"},
        )
    ).json()
    o2 = (
        await sapi.call(
            "POST",
            "/objectives",
            json={"scope_type": "workspace", "scope_id": "acme", "title": "Stabilise", "period": "2026-Q4"},
        )
    ).json()
    kr = await sapi.call(
        "POST",
        f"/objectives/{o1['id']}/key-results",
        json={"title": "NPS", "target_value": "60", "unit": "pts"},
    )
    assert kr.status_code == 201
    upd = await sapi.c.patch(
        sapi.url(f"/objectives/{o1['id']}/key-results/{kr.json()['id']}"),
        params={"current_value": "42"},
        headers={"X-Debug-Actor": "@mahmoud"},
    )
    assert upd.status_code == 200 and Decimal(upd.json()["current_value"]) == 42
    for o in (o1, o2):
        r = await sapi.call(
            "POST",
            "/classifications",
            json={
                "target_type": "work_item",
                "target_id": "WEB-101",
                "dimension": "objective",
                "value": o["id"],
            },
        )
        assert r.status_code == 201, r.text
    cl = (
        await sapi.call("GET", "/classifications", params={"target": "WEB-101", "dimension": "objective"})
    ).json()["data"]
    assert len(cl) == 2


async def test_missing_evidence_blocks_managers_but_not_reporters(sapi: Api, seed_sf: SF) -> None:
    from tt_schema.base import new_id

    async with seed_sf() as s:
        proc = await s.scalar(select(m.Process))
        build = await s.scalar(select(m.Stage).where(m.Stage.key == "build"))
        test = await s.scalar(select(m.Stage).where(m.Stage.key == "test"))
        assert proc and build and test
        s.add(
            m.StageTransitionRule(
                id=new_id(),
                process_id=proc.id,
                from_stage_id=build.id,
                to_stage_id=test.id,
                allowed_roles=[],
                required_evidence_kinds=["pr"],
            )
        )
        await s.commit()
    snap = load_snapshot()
    key = next(i["key"] for i in snap["items"] if i["stage"] == "build" and i["evidence_count"] == 0)
    r = await sapi.call("PUT", f"/items/{key}/stage", json={"stage": "test"})
    assert r.status_code == 409 and r.json()["error"]["details"]["missing"] == ["pr"]
    ok = await sapi.send(
        as_="@mahmoud", action="evidence", item={"key": key}, evidence=[{"kind": "pr", "url": "https://x/1"}]
    )
    assert ok["status"] == "applied"
    assert (await sapi.call("PUT", f"/items/{key}/stage", json={"stage": "test"})).status_code == 200


async def test_total_events_are_all_catalogued_after_the_overlay_flows(sapi: Api, seed_sf: SF) -> None:
    from tt_server.events.catalogue import is_known

    await sapi.call("PUT", "/projects/GOV/priority", json={"priority": "high"})
    await sapi.call("PUT", "/items/WEB-101/stage", json={"stage": "design"})
    await sapi.call("POST", "/risks", json={"project": "WEB", "title": "x", "probability": 2, "impact": 2})
    async with seed_sf() as s:
        types = set((await s.scalars(select(m.Event.event_type))).all())
    assert types and all(is_known(t) for t in types), [t for t in types if not is_known(t)]
    _ = TargetType


async def test_milestone_completion_is_gated_by_an_approval(sapi: Api) -> None:
    ms = (await sapi.call("GET", "/milestones")).json()["data"]
    gov = next(x for x in ms if x["project_key"] == "GOV")
    req = await sapi.call("POST", f"/milestones/{gov['id']}/approval")
    assert req.status_code == 201 and req.json()["status"] == "pending"
    assert (await sapi.call("POST", f"/milestones/{gov['id']}/approval")).status_code == 409
    blocked = await sapi.call("POST", f"/milestones/{gov['id']}/complete")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "approval_required"
    ok = await sapi.call("POST", f"/approvals/{req.json()['id']}/decisions", json={"decision": "approved"})
    assert ok.json()["status"] == "approved"
    assert (await sapi.call("POST", f"/milestones/{gov['id']}/complete")).json()["completed_at"]


async def test_new_items_continue_the_seeded_key_sequence(sapi: Api) -> None:
    """UX review "also": a new WEB item must not be WEB-1 beside the seeded WEB-131."""
    rows = (await sapi.call("GET", "/items", params={"project": "WEB", "limit": 200})).json()["data"]
    top = max(int(r["key"].rsplit("-", 1)[1]) for r in rows)
    res = await sapi.send(action="create", item={"title": "continues the sequence"}, project="WEB")
    assert int(res["item"]["key"].rsplit("-", 1)[1]) == top + 1
