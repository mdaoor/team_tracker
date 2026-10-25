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
