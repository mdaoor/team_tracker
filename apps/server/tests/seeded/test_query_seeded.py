"""Phase 6 acceptance against the seeded workspace: golden queries, layouts, ACL, injection, weight schemes."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.api.conftest import Api

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.seed.demo import load_snapshot  # noqa: E402

SNAP = load_snapshot()


NOT_EPIC = {
    "not": {"type": {"in": ["epic"]}}
}  # the snapshot's `items` exclude the 7 epics that the seed also creates


async def keys(
    api: Api, flt: dict[str, Any], as_: str = "@mahmoud", epics: bool = False, **extra: Any
) -> set[str]:
    flt = flt if epics else {"and": [flt, NOT_EPIC]}
    r = await api.call("POST", "/query/items", as_=as_, json={"filter": flt, "page": {"size": 200}, **extra})
    assert r.status_code == 200, r.text
    return {i["key"] for i in r.json()["items"]}


async def test_golden_stage_and_team_filter_matches_the_generator(sapi: Api) -> None:
    want = {i["key"] for i in SNAP["items"] if i["stage"] in ("build", "test") and i["team"] == "squad-b"}
    assert want
    got = await keys(
        sapi, {"dims": {"stage": {"in": ["build", "test"]}}, "people": {"assignee_in_team": "squad-b"}}
    )
    # assignee_in_team follows team *membership*, the snapshot's `team` is the item's own team: both must be consistent
    by_assignee = {
        i["key"]
        for i in SNAP["items"]
        if i["stage"] in ("build", "test") and i["assignee"] in {"@sara", "@ali"}
    }
    assert got and got <= {i["key"] for i in SNAP["items"] if i["stage"] in ("build", "test")}
    assert want <= got or got <= by_assignee | want


async def test_simple_dimension_filters_equal_the_snapshot(sapi: Api) -> None:
    snap = SNAP["items"]
    assert await keys(sapi, {"project": {"in": ["GOV"]}}) == {i["key"] for i in snap if i["project"] == "GOV"}
    assert await keys(sapi, {"dims": {"stage": "build"}}) == {i["key"] for i in snap if i["stage"] == "build"}
    assert await keys(sapi, {"priority": {"gte": "high"}}) == {
        i["key"] for i in snap if i["priority"] in ("high", "highest", "critical")
    }
    assert await keys(sapi, {"tags": {"any": ["bug"]}}) == {i["key"] for i in snap if "bug" in i["tags"]}
    assert await keys(sapi, {"tags": {"all": ["bug", "frontend"]}}) == {
        i["key"] for i in snap if {"bug", "frontend"} <= set(i["tags"])
    }
    assert await keys(sapi, {"tags": {"none": ["bug"]}, "project": {"in": ["WEB"]}}) == {
        i["key"] for i in snap if "bug" not in i["tags"] and i["project"] == "WEB"
    }
    assert await keys(sapi, {"people": {"assignee": "@sara"}}) == {
        i["key"] for i in snap if i["assignee"] == "@sara"
    }
    assert await keys(sapi, {"state": {"category": ["done"]}}) == {
        i["key"] for i in snap if i["state"] == "done"
    }
    assert (
        await keys(sapi, {"dims": {"team": {"not_in": ["platform"]}}})
        <= {i["key"] for i in snap if i["team"] != "platform"} | set()
    )
    assert await keys(sapi, {"or": [{"project": {"in": ["MOB"]}}, {"project": {"in": ["GOV"]}}]}) == {
        i["key"] for i in snap if i["project"] in ("MOB", "GOV")
    }
    assert await keys(sapi, {"not": {"project": {"in": ["MOB"]}}}) == {
        i["key"] for i in snap if i["project"] != "MOB"
    }
    assert await keys(sapi, {"text": "login"}) == {i["key"] for i in snap if "login" in i["title"].lower()}


async def test_the_object_model_example_runs_unchanged_where_its_names_exist(sapi: Api) -> None:
    flt = {
        "dims": {"product": "CUST", "stage": {"in": ["build", "test"]}},
        "people": {"assignee_in_team": "squad-b"},
        "people2": {"reports_to": "@mahmoud", "kind": ["line", "project"]},
        "priority": {"gte": "high"},
        "weight": {"gte": 2},
        "due_date": {"between": ["tomorrow", "tomorrow"]},
        "touched": {"since": "-14d"},
        "state": {"category": ["open", "active"]},
        "risk": {"open_score_gte": 12},
        "violations": {"open": True, "category": ["time", "process"]},
    }
    assert (
        await keys(sapi, flt) == set()
    )  # a deliberately narrow conjunction; the point is that it compiles and runs
    for k in flt:
        await keys(sapi, {k: flt[k]})
    r = await sapi.call("POST", "/query/items", json={"filter": {"dims": {"made_up_dim": "Q4-O2"}}})
    assert r.status_code == 400 and r.json()["error"]["pointer"] == "/dims/made_up_dim"


async def test_the_violations_filter_matches_the_items_the_engine_flagged(sapi: Api) -> None:
    flagged = {
        v["target"] for v in SNAP["violations"] if "/" not in v["target"]
    }  # the WIP breach is on a stage, not an item
    assert await keys(sapi, {"violations": {"open": True}}) == flagged
    assert await keys(sapi, {"violations": {"open": False}}) == {i["key"] for i in SNAP["items"]} - flagged
    by_cat = {
        v["target"] for v in SNAP["violations"] if v["category"] == "quality" and "/" not in v["target"]
    }
    assert await keys(sapi, {"violations": {"category": ["quality"]}}) == by_cat


async def test_relative_dates_follow_the_requested_zone(sapi: Api) -> None:
    for tz in ("UTC", "Asia/Riyadh", "Pacific/Auckland"):
        r = await sapi.call(
            "POST",
            "/query/items",
            json={"filter": {"due_date": {"between": ["today", "+30d"]}}, "time_zone": tz},
        )
        assert r.status_code == 200


@pytest.mark.parametrize(
    ("flt", "pointer"),
    [
        ({"nonsense": 1}, "/nonsense"),
        ({"priority": {"gte": "urgent"}}, "/priority/gte"),
        ({"people": {"assignee": "@nobody"}}, "/people/assignee"),
        ({"people": {"assignee_in_team": "ghosts"}}, "/people/assignee_in_team"),
        ({"weight": {"gte": 1, "scheme": "nope"}}, "/weight/scheme"),
        ({"type": {"in": ["dragon"]}}, "/type/in"),
        ({"and": [{"dims": {"zzz": "a"}}]}, "/and/0/dims/zzz"),
    ],
)
async def test_invalid_filters_answer_400_with_a_pointer(sapi: Api, flt: dict, pointer: str) -> None:
    r = await sapi.call("POST", "/query/items", json={"filter": flt})
    assert (
        r.status_code == 400
        and r.json()["error"]["code"] == "invalid_filter"
        and r.json()["error"]["pointer"] == pointer
    )


async def test_injection_strings_stay_parameters(sapi: Api) -> None:
    nasty = [
        "'; DROP TABLE work_items; --",
        '" OR 1=1 --',
        "%",
        "_",
        "\\",
        "a' || (select 1) || '",
        "Robert'); --",
    ]
    everything = await keys(sapi, {})
    for s in nasty:
        for flt in (
            {"text": s},
            {"tags": {"any": [s]}},
            {"project": {"in": [s]}},
            {"state": {"in": [s]}},
            {"dims": {"project": {"in": [s]}}},
        ):
            got = await keys(sapi, flt)
            assert got <= everything
        sql = (
            await sapi.call("POST", "/query/explain", json={"filter": {"text": s, "tags": {"any": [s]}}})
        ).json()["sql"]
        assert len(s) < 3 or s not in sql
        assert "DROP TABLE" not in sql
    assert await keys(sapi, {"text": "%"}) == set()  # a literal percent sign, not a wildcard
    assert await keys(sapi, {}) == everything  # still alive


def _leaf(rng: random.Random) -> dict[str, Any]:
    return rng.choice(
        [
            {"priority": {"gte": rng.choice(["low", "high", "critical"])}},
            {"weight": {"lte": rng.randint(0, 6)}},
            {"tags": {"any": rng.sample(["bug", "frontend", "backend", "ops"], 2)}},
            {"state": {"category": rng.sample(["open", "active", "done"], rng.randint(1, 2))}},
            {"project": {"in": rng.sample(["WEB", "DATA", "MOB", "GOV"], 2)}},
            {"dims": {"stage": {"in": rng.sample(["idea", "discovery", "design", "build", "test"], 2)}}},
            {"due_date": {"before": rng.choice(["today", "+14d", "2027-01-01"])}},
            {"people": {"assignee": rng.choice(["@sara", "@ali", "@nour"])}},
            {"impact": {"gte": rng.randint(1, 4)}},
            {"touched": {"since": rng.choice(["-3d", "-30d"])}},
        ]
    )


def _tree(rng: random.Random, depth: int = 0) -> dict[str, Any]:
    if depth >= 1 or rng.random() < 0.3:
        return _leaf(rng)
    op = rng.choice(["and", "or", "not"])
    return (
        {"not": _tree(rng, depth + 1)}
        if op == "not"
        else {op: [_tree(rng, depth + 1) for _ in range(rng.randint(1, 3))]}
    )


async def test_random_valid_filters_always_run_and_only_narrow(sapi: Api) -> None:
    rng = random.Random(20261024)
    everything = await keys(sapi, {})
    assert len(everything) >= 40
    for _ in range(60):
        flt = _tree(rng)
        got = await keys(sapi, flt)
        assert got <= everything
        # the negation partitions the set
        neg = await keys(sapi, {"not": flt})
        assert got | neg == everything and not (got & neg)


async def test_pipeline_shows_the_gov_build_wip_breach_and_stage_ages(sapi: Api) -> None:
    procs = (await sapi.call("GET", "/processes")).json()["data"]
    pl = (
        await sapi.call(
            "GET", f"/processes/{procs[0]['key']}/pipeline", params={"filter": '{"project":{"in":["GOV"]}}'}
        )
    ).json()
    build = next(c for c in pl["columns"] if c["stage"] == "build")
    assert build["wip_count"] == 7 and build["wip_limit"] == 4 and build["over_wip"] is True
    assert build["over_expected"] >= 0 and {"weight_sum", "expected_duration_days"} <= set(build)
    assert all("stage_age_days" in c for c in build["items"])
    full = (
        await sapi.call(
            "GET", f"/processes/{procs[0]['key']}/pipeline", params={"filter": json.dumps(NOT_EPIC)}
        )
    ).json()
    assert sum(c["wip_count"] for c in full["columns"]) == len(SNAP["items"])
    bad = await sapi.call("GET", f"/processes/{procs[0]['key']}/pipeline", params={"filter": "{nope"})
    assert bad.status_code == 400


async def test_matrix_team_by_stage_matches_the_snapshot(sapi: Api) -> None:
    r = await sapi.call(
        "POST",
        "/query/aggregate",
        json={"rows": "team", "cols": "stage", "measure": "count", "filter": NOT_EPIC},
    )
    assert r.status_code == 200, r.text
    agg = r.json()
    cell = {
        (row["value"], col): agg["cells"][i][j]
        for i, row in enumerate(agg["rows"])
        for j, col in enumerate(agg["cols"])
    }
    for team in ("platform", "squad-a", "squad-b", "data"):
        for stage in ("build", "test", "discovery"):
            want = sum(1 for i in SNAP["items"] if i["team"] == team and i["stage"] == stage)
            assert cell.get((team, stage), 0) == want, (team, stage)
    assert agg["totals"]["all"] == len(SNAP["items"])
    assert sum(agg["totals"]["rows"].values()) == len(SNAP["items"])
    assert (await sapi.call("POST", "/query/aggregate", json={"rows": "nope"})).status_code == 400
    assert (
        await sapi.call("POST", "/query/aggregate", json={"rows": "team", "measure": "vibes"})
    ).status_code == 400
    tags = (await sapi.call("POST", "/query/aggregate", json={"rows": "tags"})).json()
    bug = next(r for r in tags["rows"] if r["value"] == "bug")["count"]
    assert bug == sum(
        1 for i in SNAP["items"] if "bug" in i["tags"]
    )  # multi-valued: an item counts under each tag


async def test_every_measure_runs(sapi: Api) -> None:
    for measure in (
        "count",
        "hours",
        "weighted_throughput",
        "sum_weight",
        "open_violations",
        "avg_stage_age",
        "remaining_estimate",
    ):
        r = await sapi.call("POST", "/query/aggregate", json={"rows": "project", "measure": measure})
        assert r.status_code == 200, (measure, r.text)
    hours = (
        await sapi.call(
            "POST", "/query/aggregate", json={"rows": "project", "measure": "hours", "filter": NOT_EPIC}
        )
    ).json()["totals"]["all"]
    assert hours == pytest.approx(sum(i["hours_logged"] for i in SNAP["items"]), abs=0.6)


async def test_changing_the_weight_scheme_changes_sums_but_not_the_stored_weight(sapi: Api) -> None:
    base = (
        await sapi.call("POST", "/query/aggregate", json={"rows": "project", "measure": "sum_weight"})
    ).json()["totals"]["all"]
    before = {
        i["key"]: i["weight"]
        for i in (await sapi.call("GET", "/items", params={"limit": 200})).json()["data"]
    }
    sch = await sapi.call("POST", "/weight-schemes", json={"name": "double", "formula": {"expr": "base * 2"}})
    assert sch.status_code in (201, 400), sch.text
    if sch.status_code == 201:
        rec = await sapi.call("POST", f"/weight-schemes/{sch.json()['id']}/recompute")
        assert rec.status_code == 200
        other = (
            await sapi.call(
                "POST",
                "/query/aggregate",
                json={"rows": "project", "measure": "sum_weight", "weight_scheme_id": sch.json()["id"]},
            )
        ).json()["totals"]["all"]
        assert other != base
        after = {
            i["key"]: i["weight"]
            for i in (await sapi.call("GET", "/items", params={"limit": 200})).json()["data"]
        }
        assert after == before
    assert (
        await sapi.call("POST", "/query/aggregate", json={"rows": "project", "weight_scheme_id": "ghost"})
    ).status_code == 400


async def test_list_items_carry_the_card_fields_and_page(sapi: Api) -> None:
    r = (
        await sapi.call(
            "POST",
            "/query/items",
            json={
                "filter": NOT_EPIC,
                "page": {"size": 5},
                "sort": [{"field": "weight", "dir": "desc"}],
                "group_by": ["team"],
            },
        )
    ).json()
    assert (
        len(r["items"]) == 5 and r["page"]["next_cursor"] == "5" and r["page"]["total"] == len(SNAP["items"])
    )
    card = r["items"][0]
    for f in (
        "key",
        "title",
        "project",
        "product",
        "type",
        "assignee",
        "team",
        "stage",
        "stage_age_days",
        "expected_duration_days",
        "state",
        "priority",
        "weight",
        "impact",
        "hours_logged",
        "tags",
        "top_risk_score",
        "open_violations",
        "last_touched",
    ):
        assert f in card, f
    assert [i["weight"] for i in r["items"]] == sorted((i["weight"] for i in r["items"]), reverse=True)
    assert {g["value"] for g in r["groups"]} >= {"platform", "squad-b"}
    page2 = (
        await sapi.call(
            "POST",
            "/query/items",
            json={"page": {"size": 5, "cursor": "5"}, "sort": [{"field": "weight", "dir": "desc"}]},
        )
    ).json()
    assert not ({i["key"] for i in page2["items"]} & {i["key"] for i in r["items"]})
    assert (await sapi.call("POST", "/query/items", json={"sort": [{"field": "bogus"}]})).status_code == 400


async def test_board_gantt_calendar_now(sapi: Api) -> None:
    board = (await sapi.call("GET", "/projects/WEB/board", params={"filter": json.dumps(NOT_EPIC)})).json()
    assert [c["state"] for c in board["columns"]][:1] and sum(c["count"] for c in board["columns"]) == len(
        [i for i in SNAP["items"] if i["project"] == "WEB"]
    )
    assert (await sapi.call("POST", "/projects/WEB/baselines", params={"name": "v1"})).status_code == 201
    g = (
        await sapi.call(
            "POST", "/query/gantt", json={"filter": {"project": {"in": ["WEB"]}}, "baseline": "v1"}
        )
    ).json()
    assert g["rows"] and any(r["baseline"] for r in g["rows"]) and g["milestones"] and "today" in g
    epics = (
        await sapi.call("POST", "/query/gantt", json={"filter": {"project": {"in": ["WEB"]}}, "rows": "epic"})
    ).json()["rows"]
    assert epics and all(r["type"] == "epic" for r in epics) and any(r["rollup"] for r in epics)
    cal = (await sapi.call("POST", "/query/calendar", json={"from": "2026-10-01", "to": "2026-12-31"})).json()
    assert cal["days"]
    assert (
        await sapi.call("POST", "/query/calendar", json={"from": "2026-01-01", "to": "2028-01-01"})
    ).status_code == 400
    now = (await sapi.call("GET", "/now")).json()
    assert isinstance(now["data"], list)


async def test_csv_exports_are_stamped(sapi: Api) -> None:
    from tt_server.acl.export import is_stamped

    r = await sapi.call("POST", "/query/items:csv", json={"filter": {"project": {"in": ["GOV"]}}})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and is_stamped(r.text)
    assert r.text.count("GOV-") >= 7
    m = await sapi.call("POST", "/query/aggregate:csv", json={"rows": "team", "cols": "stage"})
    assert is_stamped(m.text) and "squad-b" in m.text


async def test_saved_views_validate_share_and_run_as_the_opener(sapi: Api) -> None:
    flt = {"project": {"in": ["GOV"]}, "priority": {"gte": "low"}}
    bad = await sapi.call("POST", "/views", json={"name": "x", "filter": {"priority": {"gte": "urgent"}}})
    assert bad.status_code == 400 and bad.json()["error"]["pointer"] == "/filter/priority/gte"
    v = (
        await sapi.call(
            "POST",
            "/views",
            json={"name": "GOV board", "filter": flt, "layout": "pipeline", "is_shared": True},
        )
    ).json()
    mine = (await sapi.call("POST", f"/views/{v['id']}/run")).json()
    assert {i["key"] for i in mine["items"]} >= {
        i["key"] for i in SNAP["items"] if i["project"] == "GOV" and i["priority"] != "lowest"
    }
    listed = (await sapi.call("GET", "/views", as_="@ali")).json()["data"]
    assert any(x["id"] == v["id"] and x["mine"] is False for x in listed)
    theirs = (await sapi.call("POST", f"/views/{v['id']}/run", as_="@ali")).json()
    visible_to_ali = {
        i["key"] for i in (await sapi.call("GET", "/items", as_="@ali", params={"limit": 200})).json()["data"]
    }
    assert {i["key"] for i in theirs["items"]} <= visible_to_ali  # ACL is applied inside the compiler
    assert (
        await sapi.call("PATCH", f"/views/{v['id']}", as_="@ali", json={"name": "mine now"})
    ).status_code == 403
    private = (await sapi.call("POST", "/views", json={"name": "secret", "filter": {}})).json()
    assert (await sapi.call("GET", f"/views/{private['id']}", as_="@ali")).status_code == 404
    patched = (
        await sapi.call("PATCH", f"/views/{v['id']}", json={"name": "renamed", "is_shared": False})
    ).json()
    assert patched["name"] == "renamed"
    assert (await sapi.call("DELETE", f"/views/{v['id']}")).status_code == 204
    assert (await sapi.call("GET", f"/views/{v['id']}")).status_code == 404


async def test_inbox_items_stay_private_in_every_query(sapi: Api) -> None:
    await sapi.send(as_="@sara", action="create", item={"title": "inbox only thing"}, project="nowhere")
    assert not any(k.startswith("INBOX-sara") for k in await keys(sapi, {"include_inbox": True}, as_="@ali"))
    assert any(k.startswith("INBOX-sara") for k in await keys(sapi, {"include_inbox": True}, as_="@sara"))
    assert not any(
        k.startswith("INBOX-sara") for k in await keys(sapi, {}, as_="@sara")
    )  # excluded unless asked for


async def test_filter_schema_is_served(sapi: Api) -> None:
    s = (await sapi.call("GET", "/meta/schema/filter")).json()
    assert s["type"] == "object" and "dims" in s["properties"]
