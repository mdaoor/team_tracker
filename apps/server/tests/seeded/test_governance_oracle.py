"""Phase 8 acceptance oracle: the seeded workspace through the real engine reproduces `tools/gen_snapshot.py`'s violations and habits."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

from tests.api.conftest import Api

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools.seed.demo import load_snapshot  # noqa: E402

SNAP = load_snapshot()
KEYS = ("policy", "category", "severity", "target", "person", "team", "project", "detail")


def row(v: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(v[k] for k in KEYS)


async def snapshot(sapi: Api, **params: Any) -> dict[str, Any]:
    r = await sapi.call(
        "GET", "/snapshot", params={"as_of": "2026-10-23T18:30:00+03:00", "fresh": 1, **params}
    )
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_violations_equal_the_generators_exactly(sapi: Api) -> None:
    got = Counter(row(v) for v in (await snapshot(sapi))["violations"])
    want = Counter(row(v) for v in SNAP["violations"])
    assert sum(want.values()) == 36
    assert got == want, {
        "missing": list((want - got).elements())[:5],
        "extra": list((got - want).elements())[:5],
    }
    wip = [v for v in SNAP["violations"] if v["policy"] == "wip_breach"]
    assert len(wip) == 1 and wip[0]["target"] == "GOV/build" and row(wip[0]) in got


async def test_deterministic_habits_equal_the_generators(sapi: Api) -> None:
    s = await snapshot(sapi)
    got = {(h["subject"], h["key"], h["polarity"]) for h in s["habits"]}
    want = {
        (h["subject"], h["key"], h["polarity"])
        for h in SNAP["habits"]
        if h["key"] in {"no_estimates", "late_finish", "evidence_consistency"}
    }
    assert want and want <= got, {"missing": sorted(want - got)}
    # nothing else of the three deterministic kinds is invented
    assert {x for x in got if x[1] in {"no_estimates", "late_finish", "evidence_consistency"}} == want


async def test_violations_are_listed_with_filters_and_acl(sapi: Api) -> None:
    r = await sapi.call("GET", "/violations", params={"status": "open", "limit": 500})
    assert r.status_code == 200 and len(r.json()["data"]) == 36
    stuck = (await sapi.call("GET", "/violations", params={"policy": "stuck_in_stage"})).json()["data"]
    assert stuck and all(v["policy"] == "stuck_in_stage" for v in stuck)
    sara = (
        await sapi.call("GET", "/violations", as_="@sara", params={"person": "@sara", "limit": 500})
    ).json()["data"]
    assert all(v["person"] == "@sara" for v in sara)
