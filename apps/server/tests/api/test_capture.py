from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m

from tests.api.conftest import Api


async def count(sf: async_sessionmaker[AsyncSession], model: type, *where: object) -> int:
    async with sf() as s:
        return int(await s.scalar(select(func.count()).select_from(model).where(*where)) or 0)  # type: ignore[arg-type]


async def test_ux_7_1_start_creates_item_activity_and_four_events(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    first = await api.send(action="start", item={"title": "something else first"}, project="WEB")
    assert first["status"] == "applied"
    res = await api.send(
        action="start",
        item={"title": "picking up the retry refactor on the pipeline"},
        project="the pipeline",
        estimate={"value": 2, "unit": "days"},
    )
    assert res["status"] == "applied" and res["item"]["project_key"] == "DATA"
    assert (
        res["resolution"]["project"]["method"] == "glossary"
        and res["resolution"]["item"]["method"] == "created"
    )
    # the previous activity got paused, new one is active
    async with session_factory() as s:
        acts = (await s.scalars(select(m.Activity).order_by(m.Activity.started_at, m.Activity.id))).all()
        assert [a.status.value for a in acts] == ["paused", "active"]
        est = (await s.scalars(select(m.Estimate))).one()
        assert (str(est.value), est.unit.value, est.kind.value) == ("2.0000", "days", "original")
    async with session_factory() as s:
        corr = await s.scalar(select(m.Event.correlation_id).where(m.Event.client_id == res["client_id"]))
        types = sorted(
            (await s.scalars(select(m.Event.event_type).where(m.Event.correlation_id == corr))).all()
        )
    # plan §1.4 lists four events; the open->active state move and the default-stage placement (Phase 3) add two
    assert types == [
        "activity.paused",
        "activity.started",
        "estimate.set",
        "item.created",
        "item.state_changed",
        "stage.moved",
    ]


async def test_matches_existing_item_by_fuzzy_title(api: Api) -> None:
    created = await api.send(action="create", item={"title": "fix the login redirect loop"}, project="WEB")
    hit = await api.send(action="start", item={"title": "fix login redirect"}, project="WEB")
    assert hit["item"]["key"] == created["item"]["key"] and hit["resolution"]["item"]["method"] == "matched"
    miss = await api.send(action="start", item={"title": "fix checkout"}, project="WEB")
    assert miss["item"]["key"] != created["item"]["key"] and miss["resolution"]["item"]["method"] == "created"


async def test_idempotent_by_client_id(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    msg = {
        "actor": "@mahmoud",
        "at": "2026-10-23T10:00:00+03:00",
        "client_id": "same-1",
        "action": "start",
        "item": {"title": "write the docs"},
        "minutes": 30,
    }
    body = {"workspace": "acme", "messages": [msg]}
    r1 = await api.call("POST", "/capture/batches", json=body)
    before = (
        await count(session_factory, m.WorkItem),
        await count(session_factory, m.Activity),
        await count(session_factory, m.TimeEntry),
        await count(session_factory, m.Event),
    )
    r2 = await api.call("POST", "/capture/batches", json=body)
    after = (
        await count(session_factory, m.WorkItem),
        await count(session_factory, m.Activity),
        await count(session_factory, m.TimeEntry),
        await count(session_factory, m.Event),
    )
    assert before == after
    res2 = r2.json()["results"][0]
    assert res2["status"] == "deduplicated" and res2["item"] == r1.json()["results"][0]["item"]


async def test_never_blocks_unknown_project_person_and_missing_estimate(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    res = await api.send(
        action="create",
        item={"title": "mystery work"},
        project="no such project",
        assignees=[{"person": "@ghost", "role": "owner"}],
        tags=["new-tag"],
    )
    assert res["status"] == "applied" and res["item"]["project_key"] == "INBOX-mahmoud"
    assert len(res["review_item_ids"]) == 3  # unclassified project phrase, provisional tag, unknown person
    async with session_factory() as s:
        reviews = {r.kind: r for r in (await s.scalars(select(m.ReviewItem))).all()}
        assert set(reviews) == {"unknown_person", "unclassified_project", "provisional_tag"}
        assert reviews["unknown_person"].suggestion["person"] == "@ghost"
        assert reviews["unclassified_project"].suggestion["phrase"] == "no such project"
        item = await s.scalar(select(m.WorkItem).where(m.WorkItem.key == res["item"]["key"]))
        assert item is not None and item.is_provisional
        tag = (await s.scalars(select(m.Tag))).one()
        assert tag.is_provisional and tag.name == "new-tag"


async def test_done_then_start_reopens(api: Api) -> None:
    started = await api.send(action="start", item={"title": "ship the thing"}, project="WEB")
    key = started["item"]["key"]
    done = await api.send(action="done", item={"key": key})
    assert done["status"] == "applied"
    r = await api.call("GET", f"/items/{key}")
    d = r.json()
    assert (
        d["closed_at"] is not None
        and d["actual_end"] is not None
        and d["latest_activity"]["status"] == "ended"
    )
    again = await api.send(action="start", item={"key": key})
    d2 = (await api.call("GET", f"/items/{key}")).json()
    assert d2["closed_at"] is None and d2["actual_end"] is None
    assert again["resolution"]["item"]["method"] == "matched"


async def test_reopen_emits_event(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    key = (await api.send(action="start", item={"title": "reopen me"}, project="WEB"))["item"]["key"]
    await api.send(action="done", item={"key": key})
    await api.send(action="start", item={"key": key})
    async with session_factory() as s:
        types = (await s.scalars(select(m.Event.event_type))).all()
    assert "item.reopened" in types and "item.finished" in types


async def test_log_time_uses_person_local_date(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    # 22:30 UTC is 01:30 next day in Asia/Riyadh (+03)
    await api.send(
        action="log_time", item={"title": "late night"}, project="WEB", minutes=45, at="2026-10-23T22:30:00Z"
    )
    async with session_factory() as s:
        te = (await s.scalars(select(m.TimeEntry))).one()
    assert te.date.isoformat() == "2026-10-24" and te.minutes == 45


async def test_note_evidence_pause_resume_blocked_plan(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    key = (await api.send(action="start", item={"title": "evidence work"}, project="DATA"))["item"]["key"]
    await api.send(action="note", item={"key": key}, note="found the root cause")
    await api.send(
        action="evidence",
        item={"key": key},
        evidence=[{"kind": "pr", "url": "https://x/pr/1"}, {"kind": "weird"}],
    )
    await api.send(action="pause", item={"key": key})
    await api.send(action="resume", item={"key": key})
    await api.send(action="blocked", item={"key": key}, note="waiting on infra")
    await api.send(action="plan", item={"key": key}, planned_end="2026-10-25T17:00:00+03:00")
    async with session_factory() as s:
        kinds = sorted(e.kind.value for e in (await s.scalars(select(m.Evidence))).all())
        item = await s.scalar(select(m.WorkItem).where(m.WorkItem.key == key))
        acts = (await s.scalars(select(m.Activity).order_by(m.Activity.started_at, m.Activity.id))).all()
    assert kinds == ["note", "note", "pr"]
    assert item is not None and item.planned_end is not None and item.planned_end.isoformat() == "2026-10-25"
    assert acts[-1].kind.value == "blocked" and acts[-1].note == "waiting on infra"


async def test_overlay_hints_and_low_confidence(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    res = await api.send(
        action="create",
        item={"title": "hinted"},
        project="WEB",
        priority="high",
        tags=["a", "b"],
        item_type="bug",
        llm_confidence={"priority": 0.4},
    )
    async with session_factory() as s:
        item = await s.scalar(select(m.WorkItem).where(m.WorkItem.key == res["item"]["key"]))
        reviews = (await s.scalars(select(m.ReviewItem))).all()
    assert item is not None and item.priority.value == "medium"  # 0.4 < 0.7: not applied
    assert [r.kind for r in reviews if r.kind != "provisional_tag"] == ["low_confidence"]
    res2 = await api.send(action="create", item={"title": "hinted 2"}, project="WEB", priority="high")
    d = (await api.call("GET", f"/items/{res2['item']['key']}")).json()
    assert d["priority"] == "high" and d["priority_source"] == "rule"


async def test_parent_hint_and_subtree_move(api: Api) -> None:
    epic = await api.send(action="create", item={"title": "the epic"}, project="WEB", item_type="epic")
    child = await api.send(
        action="create",
        item={"title": "the child"},
        project="WEB",
        item_type="task",
        parent={"key": epic["item"]["key"]},
    )
    d = (await api.call("GET", f"/items/{child['item']['key']}")).json()
    assert d["parent_id"] and d["path"].count(".") == 1


async def test_on_behalf_of_requires_relationship(api: Api) -> None:
    res = await api.send(action="create", item={"title": "for sara"}, project="WEB", on_behalf_of="@sara")
    assert "on_behalf_of_ignored" in res["warnings"] and res["status"] == "applied_with_warnings"
    r = await api.call(
        "POST",
        "/reporting-lines",
        json={"person_id": (await _pid(api, "@sara")), "manager_id": (await _pid(api, "@mahmoud"))},
    )
    assert r.status_code == 201
    res2 = await api.send(action="create", item={"title": "for sara 2"}, project="WEB", on_behalf_of="@sara")
    assert res2["warnings"] == []
    d = (await api.call("GET", f"/items/{res2['item']['key']}")).json()
    assert d["assignee_id"] == await _pid(api, "@sara") and d["reporter_id"] == await _pid(api, "@mahmoud")


async def _pid(api: Api, handle: str) -> str:
    people = (await api.call("GET", "/people")).json()["data"]
    return next(p["person_id"] for p in people if p["handle"] == handle)


async def test_schema_violation_is_422_with_message_index(api: Api) -> None:
    body = {
        "workspace": "acme",
        "messages": [
            {"actor": "@mahmoud", "action": "start", "at": "2026-10-23T10:00:00Z", "client_id": "ok-1"},
            {"actor": "@mahmoud", "action": "teleport", "at": "2026-10-23T10:00:00Z", "client_id": "bad-1"},
        ],
    }
    r = await api.call("POST", "/capture/batches", json=body)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "schema_violation" and err["details"]["message_index"] == 1


async def test_unknown_actor_yields_207_not_batch_failure(api: Api) -> None:
    body = {
        "workspace": "acme",
        "messages": [
            {
                "actor": "@mahmoud",
                "action": "create",
                "at": "2026-10-23T10:00:00Z",
                "client_id": "m-1",
                "item": {"title": "fine"},
            },
            {
                "actor": "@nobody",
                "action": "create",
                "at": "2026-10-23T10:00:00Z",
                "client_id": "m-2",
                "item": {"title": "x"},
            },
        ],
    }
    r = await api.call("POST", "/capture/batches", json=body)
    assert r.status_code == 207
    assert [x["status"] for x in r.json()["results"]] == ["applied", "failed"]


async def test_every_emitted_event_type_is_in_the_catalogue(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    from tt_server.events.catalogue import is_known

    await api.send(
        action="start",
        item={"title": "catalogue check"},
        project="WEB",
        estimate={"value": 1, "unit": "hours"},
        minutes=10,
        tags=["z"],
    )
    async with session_factory() as s:
        types = set((await s.scalars(select(m.Event.event_type))).all())
    assert types and all(is_known(t) for t in types)
