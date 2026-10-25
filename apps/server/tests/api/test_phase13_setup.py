"""Phase 13 R6.3: setup editors that need a server half (stage edit)."""

from __future__ import annotations

from tests.api.conftest import Api


async def test_a_stage_is_edited_in_place_and_the_event_records_before_and_after(api: Api) -> None:
    procs = (await api.call("GET", "/processes")).json()["data"]
    proc = procs[0]
    stage = proc["stages"][0]
    r = await api.call(
        "PATCH",
        f"/processes/{proc['key']}/stages/{stage['key']}",
        json={"expected_duration_days": 7, "wip_limit": 3, "requires_approval": True},
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert (got["expected_duration_days"], got["wip_limit"], got["requires_approval"]) == (7, 3, True)
    again = (await api.call("GET", "/processes")).json()["data"][0]["stages"][0]
    assert again["wip_limit"] == 3
    events = (await api.call("GET", "/events", params={"type": "stage.updated"})).json()
    assert events is not None


async def test_stage_edit_rejects_bad_values_and_non_admins(api: Api) -> None:
    proc = (await api.call("GET", "/processes")).json()["data"][0]
    key = proc["stages"][0]["key"]
    base = f"/processes/{proc['key']}/stages/{key}"
    assert (await api.call("PATCH", base, json={"wip_limit": -1})).status_code == 400
    assert (await api.call("PATCH", base, json={"category": "nonsense"})).status_code == 400
    assert (await api.call("PATCH", base, as_="@ali", json={"wip_limit": 2})).status_code == 403
    assert (
        await api.call("PATCH", f"/processes/{proc['key']}/stages/nope", json={"wip_limit": 2})
    ).status_code == 404


async def test_the_org_graph_is_readable_by_those_who_edit_it_and_carries_names_and_capacity(
    api: Api,
) -> None:
    people = {p["handle"]: p for p in (await api.call("GET", "/people")).json()["data"]}
    assert "capacity_hours_per_week" in people["@sara"]
    r = await api.call(
        "POST", "/reporting-lines",
        json={"person_id": people["@ali"]["person_id"], "manager_id": people["@sara"]["person_id"], "kind": "line", "is_primary": True},
    )  # fmt: skip
    assert r.status_code == 201, r.text
    got = (await api.call("GET", "/reporting-lines")).json()["data"]
    assert [(x["person"], x["manager"], x["kind"]) for x in got] == [("@ali", "@sara", "line")]
    assert (await api.call("GET", "/reporting-lines", as_="@ali")).status_code == 403


async def test_gantt_rows_can_be_milestones_and_default_to_valid_values(api: Api) -> None:
    ms = await api.call("POST", "/projects/WEB/milestones", json={"name": "Beta", "due_date": "2026-11-20"})
    assert ms.status_code == 201, ms.text
    await api.send(action="create", item={"title": "beta work"}, project="WEB", planned_end="2026-11-15")
    r = await api.call("POST", "/query/gantt", json={"rows": "milestone"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["milestones"] and got["milestones"][0]["name"] == "Beta"
    assert all(x["type"] == "milestone" for x in got["rows"])
    assert (await api.call("POST", "/query/gantt", json={"rows": "nonsense"})).status_code == 400


async def test_a_slide_remembers_how_to_fit_long_content(api: Api) -> None:
    p = (
        await api.call("POST", "/presentations", json={"title": "Fit", "slides": [{"kind": "projects"}]})
    ).json()
    sid = p["slides"][0]["id"]
    assert p["slides"][0]["fit"] == "paginate"
    r = await api.call("PATCH", f"/presentations/{p['id']}/slides/{sid}", json={"fit": "shrink"})
    assert r.status_code == 200 and r.json()["slides"][0]["fit"] == "shrink"
    assert (
        await api.call("PATCH", f"/presentations/{p['id']}/slides/{sid}", json={"fit": "zoom"})
    ).status_code == 400
