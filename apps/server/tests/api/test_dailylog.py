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
