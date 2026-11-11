"""Plan P1 RecurrenceRule: validation, preview, idempotent materialisation, catch-up, and the nightly progress heal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.domain import recurrence as rec
from tt_server.domain.plan import linkage

from tests.api.conftest import Api
from tests.api.test_acl_plan import make

ROUTERS_COVERED = ("recurrence",)


async def template(api: Api) -> str:
    return str(
        (await api.call("POST", "/items", json={"title": "Weekly status report", "project": "WEB"})).json()[
            "key"
        ]
    )


async def count(sf: async_sessionmaker[AsyncSession], title: str) -> int:
    async with sf() as s:
        return int(
            await s.scalar(select(func.count()).select_from(m.WorkItem).where(m.WorkItem.title == title)) or 0
        )


async def test_rules_are_validated(api: Api) -> None:
    t = await template(api)
    base = {"template_item": t, "time_zone": "UTC"}
    for rrule, tz, code in (
        ("FREQ=HOURLY", "UTC", 400), ("nonsense", "UTC", 400), ("FREQ=WEEKLY", "Mars/Base", 400),
        ("FREQ=WEEKLY;COUNT=2\nRDATE:20300101T000000Z", "UTC", 400),
    ):  # fmt: skip
        r = await api.call(
            "POST", "/projects/WEB/recurrence-rules", json={**base, "rrule": rrule, "time_zone": tz}
        )
        assert r.status_code == code, (rrule, r.text)
    other = (await api.call("POST", "/items", json={"title": "elsewhere", "project": "DATA"})).json()["key"]
    r = await api.call(
        "POST", "/projects/WEB/recurrence-rules", json={**base, "rrule": "FREQ=DAILY", "template_item": other}
    )
    assert r.status_code == 400  # the template must belong to the project
    ok = await api.call(
        "POST", "/projects/WEB/recurrence-rules", json={**base, "rrule": "FREQ=WEEKLY;BYDAY=MO"}
    )
    assert ok.status_code == 201 and ok.json()["next_run_at"] and ok.json()["is_active"]


async def test_preview_lists_the_next_occurrences(api: Api) -> None:
    r = await api.call(
        "POST",
        "/recurrence-rules:preview",
        json={"rrule": "FREQ=WEEKLY;BYDAY=MO,TH", "count": 4, "start": "2026-10-24T00:00:00Z"},
    )
    days = [datetime.fromisoformat(x).strftime("%a %m-%d") for x in r.json()["occurrences"]]
    assert days == ["Mon 10-26", "Thu 10-29", "Mon 11-02", "Thu 11-05"]


async def test_materialising_is_idempotent_and_advances(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    t = await template(api)
    rule = (
        await api.call(
            "POST", "/projects/WEB/recurrence-rules", json={"template_item": t, "rrule": "FREQ=DAILY"}
        )
    ).json()
    due = datetime.fromisoformat(rule["next_run_at"])
    assert await rec.run_due(session_factory, due + timedelta(minutes=1)) == 1
    assert await count(session_factory, "Weekly status report") == 2
    assert (
        await rec.run_due(session_factory, due + timedelta(minutes=2)) == 0
    )  # nothing new until the next day
    async with session_factory() as s:
        r = await s.get(m.RecurrenceRule, rule["id"])
        assert r and r.next_run_at == due + timedelta(days=1)
        made = await s.scalar(
            select(m.WorkItem).where(m.WorkItem.source["recurrence"]["rule"].astext == rule["id"])
        )
        assert made and made.planned_end is not None
        # even if the cursor is rewound, the occurrence marker prevents a duplicate
        r.next_run_at = due
        await s.commit()
    assert await rec.run_due(session_factory, due + timedelta(minutes=3)) == 0
    assert await count(session_factory, "Weekly status report") == 2


async def test_lead_days_create_the_item_early_and_downtime_is_capped(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    t = await template(api)
    rule = (
        await api.call(
            "POST",
            "/projects/WEB/recurrence-rules",
            json={"template_item": t, "rrule": "FREQ=WEEKLY", "lead_days": 3},
        )
    ).json()
    due = datetime.fromisoformat(rule["next_run_at"])
    assert await rec.run_due(session_factory, due - timedelta(days=4)) == 0
    assert await rec.run_due(session_factory, due - timedelta(days=2)) == 1
    # a long outage: 40 weekly occurrences are overdue but only a bounded number is created
    async with session_factory() as s:
        r = await s.get(m.RecurrenceRule, rule["id"])
        assert r
        r.next_run_at = due - timedelta(weeks=40)
        await s.commit()
    made = await rec.run_due(session_factory, due)
    assert 0 < made <= rec.MAX_CATCH_UP


async def test_finite_rules_deactivate_and_patch_and_delete(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    t = await template(api)
    rule = (
        await api.call(
            "POST", "/projects/WEB/recurrence-rules", json={"template_item": t, "rrule": "FREQ=DAILY;COUNT=1"}
        )
    ).json()
    due = datetime.fromisoformat(rule["next_run_at"])
    await rec.run_due(session_factory, due + timedelta(minutes=1))
    got = (await api.call("GET", "/projects/WEB/recurrence-rules")).json()["data"][0]
    assert got["is_active"] is False and got["next_run_at"] is None
    r2 = (
        await api.call(
            "POST", "/projects/WEB/recurrence-rules", json={"template_item": t, "rrule": "FREQ=MONTHLY"}
        )
    ).json()
    off = (await api.call("PATCH", f"/recurrence-rules/{r2['id']}", json={"is_active": False})).json()
    assert off["is_active"] is False
    assert (
        await api.call("PATCH", f"/recurrence-rules/{r2['id']}", json={"rrule": "bad"})
    ).status_code == 400
    assert (await api.call("DELETE", f"/recurrence-rules/{r2['id']}")).status_code == 204
    assert (
        await api.call("PATCH", f"/recurrence-rules/{r2['id']}", json={"is_active": True})
    ).status_code == 404
    assert (
        await api.call(
            "POST",
            "/projects/WEB/recurrence-rules",
            as_="@sara",
            json={"template_item": t, "rrule": "FREQ=DAILY"},
        )
    ).status_code in (403, 404)


async def test_a_deleted_template_stops_the_rule_quietly(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    t = await template(api)
    rule = (
        await api.call(
            "POST", "/projects/WEB/recurrence-rules", json={"template_item": t, "rrule": "FREQ=DAILY"}
        )
    ).json()
    async with session_factory() as s:
        it = await s.get(m.WorkItem, rule["template_item_id"])
        assert it
        it.deleted_at = datetime.now(UTC)
        await s.commit()
    assert (
        await rec.run_due(session_factory, datetime.fromisoformat(rule["next_run_at"]) + timedelta(minutes=1))
        == 0
    )
    async with session_factory() as s:
        r = await s.get(m.RecurrenceRule, rule["id"])
        assert r and r.is_active is False


# ---------------------------------------------------------------- progress heal
async def test_nightly_recompute_heals_drift_and_records_it(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await make(api)
    async with session_factory() as s:
        d = (await s.scalars(select(m.WorkItem).where(m.WorkItem.title == "Requirements"))).one()
        dkey, did = d.key, d.id
    kids = []
    for t in ("spec", "review"):
        r = await api.call("POST", "/items", json={"title": t, "project": "ERP", "parent": dkey})
        assert r.status_code == 201, r.text
        kids.append(r.json()["key"])
    assert (await api.call("POST", f"/items/{kids[0]}/state", json={"state": "done"})).status_code == 200
    async with session_factory() as s:
        d = await s.get(m.WorkItem, did)
        assert d and d.progress_pct == 50  # the write path keeps it current
        d.progress_pct = 77  # drift, e.g. a child removed by a bulk import that bypassed the hook
        await s.commit()
    assert await linkage.recompute_all(session_factory) >= 1
    async with session_factory() as s:
        d = await s.get(m.WorkItem, did)
        assert d and d.progress_pct == 50
        ev = (
            await s.scalars(
                select(m.Event).where(
                    m.Event.event_type == "item.progress_recomputed", m.Event.entity_id == did
                )
            )
        ).all()
        assert len(ev) == 1 and ev[0].payload["before"]["progress_pct"] == 77
    assert await linkage.recompute_all(session_factory) == 0  # idempotent
