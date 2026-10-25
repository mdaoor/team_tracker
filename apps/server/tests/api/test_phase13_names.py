"""Phase 13 R2.1: display fields carry names, never bare UUIDs."""

from __future__ import annotations

import re
from typing import Any

from tests.api.conftest import Api

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def uuids_in_display(d: Any, fields: set[str], path: str = "") -> list[str]:
    """Every value stored under a display field name that is (or contains) a UUID."""
    bad: list[str] = []
    if isinstance(d, dict):
        for k, v in d.items():
            if k in fields and isinstance(v, str) and UUID.search(v):
                bad.append(f"{path}.{k}={v}")
            bad += uuids_in_display(v, fields, f"{path}.{k}")
    elif isinstance(d, list):
        for i, v in enumerate(d):
            bad += uuids_in_display(v, fields, f"{path}[{i}]")
    return bad


async def test_item_detail_names_reporter_and_assignee(api: Api) -> None:
    res = await api.send(as_="@sara", action="start", item={"title": "named"}, project="DATA")
    d = (await api.call("GET", f"/items/{res['item']['key']}")).json()
    assert d["reporter"] == "@sara" and d["assignee"] == "@sara"
    assert uuids_in_display(d, {"reporter", "assignee"}) == []


async def test_provenance_names_who_set_it_and_where_it_came_from(api: Api) -> None:
    res = await api.send(as_="@sara", action="start", item={"title": "prov"}, project="DATA", priority="high")
    key = res["item"]["key"]
    etag = (await api.call("GET", f"/items/{key}")).headers["etag"]
    r = await api.c.patch(
        api.url(f"/items/{key}"),
        headers={"X-Debug-Actor": "@mahmoud", "If-Match": etag},
        json={"priority": "highest"},
    )
    assert r.status_code == 200, r.text
    prov = (await api.call("GET", f"/items/{key}/provenance")).json()
    entries = [e for v in prov.values() if isinstance(v, list) for e in v]
    assert entries, prov
    assert all("set_by_name" in e for e in entries)
    assert uuids_in_display(prov, {"set_by_name", "name"}) == []
    assert any(e["set_by_name"] == "@mahmoud" for e in entries)


async def test_digest_events_say_which_item_and_who(api: Api) -> None:
    await api.send(as_="@sara", action="start", item={"title": "digest me"}, project="DATA")
    d = (await api.call("GET", "/digest")).json()
    assert d["events"]
    assert all("entity" in e and "actor" in e for e in d["events"])
    assert uuids_in_display(d["events"], {"entity", "actor"}) == []
    assert any(str(e["entity"]).startswith("DATA-") for e in d["events"])


async def test_habits_and_violations_label_their_targets(api: Api) -> None:
    hs = (await api.call("GET", "/habits")).json()["data"]
    assert uuids_in_display(hs, {"subject"}) == []
    vs = (await api.call("GET", "/violations")).json()["data"]
    assert uuids_in_display(vs, {"target", "team", "project", "person"}) == []


async def test_asking_for_your_own_day_drafts_the_log_when_work_was_logged(api: Api) -> None:
    await api.send(
        as_="@sara", action="log_time", item={"title": "wrote it down"}, project="DATA", minutes=45
    )
    today = "2026-10-23"  # the day Api.send stamps on its messages
    logs = (await api.call("GET", "/daily-logs", as_="@sara", params={"day": today})).json()["data"]
    assert len(logs) == 1 and logs[0]["status"] == "draft" and logs[0]["minutes_total"] == 45
    again = (await api.call("GET", "/daily-logs", as_="@sara", params={"day": today})).json()["data"]
    assert [x["id"] for x in again] == [logs[0]["id"]]  # idempotent: same draft, no duplicate
    nothing = (await api.call("GET", "/daily-logs", as_="@ali", params={"day": today})).json()["data"]
    assert nothing == []  # no work, no log


async def test_a_reporting_line_that_starts_tomorrow_counts_only_where_it_is_already_tomorrow(
    api: Api, session_factory
) -> None:  # type: ignore[no-untyped-def]
    """Review T8: validity is judged on the person's local date, not the server's."""
    from datetime import timedelta

    from sqlalchemy import select
    from tt_server.acl.reporting import reports_of
    from tt_server.clock import utc_today
    from tt_server.db import models as m

    people = {p["handle"]: p["person_id"] for p in (await api.call("GET", "/people")).json()["data"]}
    r = await api.call(
        "POST",
        "/reporting-lines",
        json={"person_id": people["@ali"], "manager_id": people["@sara"], "kind": "line"},
    )
    assert r.status_code == 201, r.text
    async with session_factory() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        line = await s.scalar(select(m.ReportingLine).where(m.ReportingLine.person_id == people["@ali"]))
        assert ws and line
        line.valid_from = utc_today() + timedelta(days=1)
        await s.flush()
        assert await reports_of(s, ws.id, people["@sara"]) == []  # not yet by the UTC clock
        assert await reports_of(s, ws.id, people["@sara"], at=utc_today() + timedelta(days=1)) == [
            people["@ali"]
        ]
