from __future__ import annotations

from datetime import date

from sqlalchemy import select
from tt_server.db import models as m
from tt_server.domain.capture import dailylog

from tests.api.conftest import Api


async def test_build_confirm_review(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    await api.send(action="start", item={"title": "daily work"}, project="WEB", minutes=90)
    await api.send(as_="@sara", action="create", item={"title": "sara work"}, project="WEB")
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace))
    day = date(2026, 10, 23)
    assert await dailylog.build_for_workspace(session_factory, ws.id, day) == 2  # @ali had no activity
    assert await dailylog.build_for_workspace(session_factory, ws.id, day) == 2  # idempotent upsert
    logs = (await api.call("GET", "/daily-logs", params={"day": "2026-10-23"})).json()["data"]
    assert len(logs) == 1 and logs[0]["status"] == "draft" and logs[0]["minutes_total"] == 90
    assert "WEB-1 daily work — 1.5h" in logs[0]["summary"]
    log_id = logs[0]["id"]
    assert (await api.call("POST", f"/daily-logs/{log_id}/confirm")).json()["status"] == "confirmed"
    # a confirmed log is never rewritten by a later build
    await api.send(action="log_time", item={"title": "daily work"}, project="WEB", minutes=30)
    await dailylog.build_for_workspace(session_factory, ws.id, day)
    assert (await api.call("GET", "/daily-logs", params={"day": "2026-10-23"})).json()["data"][0][
        "minutes_total"
    ] == 90
    # a manager who is not the subject's line manager may not review ...
    await api.role("@sara", "manager")
    assert (await api.call("POST", f"/daily-logs/{log_id}/review", as_="@sara")).status_code == 403
    # ... but the line manager can
    people = {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}
    line = {"person_id": people["@mahmoud"], "manager_id": people["@sara"], "is_primary": True}
    assert (await api.call("POST", "/reporting-lines", json=line)).status_code == 201
    assert (await api.call("POST", f"/daily-logs/{log_id}/review", as_="@sara")).json()[
        "status"
    ] == "reviewed"
    # sara cannot confirm mahmoud's log
    assert (await api.call("POST", f"/daily-logs/{log_id}/confirm", as_="@sara")).status_code == 404
    async with session_factory() as s:
        types = set(
            (await s.scalars(select(m.Event.event_type).where(m.Event.event_type.like("dailylog.%")))).all()
        )
    assert types == {"dailylog.drafted", "dailylog.confirmed", "dailylog.reviewed"}


async def test_logging_time_drafts_the_day_at_once(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    # no GET and no nightly job in between: the ingest path itself keeps the draft current
    await api.send(action="log_time", item={"title": "review"}, project="WEB", minutes=45)
    async with session_factory() as s:
        rows = (await s.scalars(select(m.DailyLog))).all()
    assert len(rows) == 1 and rows[0].date == date(2026, 10, 23) and rows[0].minutes_total == 45
    assert rows[0].status.value == "draft"
    await api.send(action="log_time", item={"title": "review"}, project="WEB", minutes=15)
    got = (await api.call("GET", "/daily-logs", params={"day": "2026-10-23"})).json()["data"]
    assert len(got) == 1 and got[0]["minutes_total"] == 60 and got[0]["status"] == "draft"


async def test_ending_an_activity_drafts_the_day_in_the_persons_zone(api: Api, session_factory) -> None:  # type: ignore[no-untyped-def]
    await api.send(action="start", item={"title": "ship"}, project="WEB")
    async with session_factory() as s:
        for row in (await s.scalars(select(m.DailyLog))).all():
            await s.delete(row)
        await s.commit()
    await api.send(action="done", item={"title": "ship"}, project="WEB")
    async with session_factory() as s:
        rows = (await s.scalars(select(m.DailyLog))).all()
    assert [r.date for r in rows] == [date(2026, 10, 23)]
