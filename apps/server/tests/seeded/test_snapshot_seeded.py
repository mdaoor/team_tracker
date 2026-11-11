"""Phase 7 golden: the assembler reproduces `tools/gen_snapshot.py` for every seed-controlled field."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import validate

from tests.api.conftest import Api

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tools.gen_schemas import render  # noqa: E402
from tools.seed.demo import load_snapshot  # noqa: E402

SNAP = load_snapshot()
AS_OF = "2026-10-23T18:30:00+03:00"
FIELDS = (
    "title",
    "project",
    "assignee",
    "stage",
    "state",
    "priority",
    "tags",
    "risks",
    "planned_end",
    "planned_start",
    "actual_end",
)


async def snapshot(api: Api, as_: str = "@mahmoud", **params: Any) -> dict[str, Any]:
    r = await api.call("GET", "/snapshot", as_=as_, params={"as_of": AS_OF, "days": 14, **params})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_items_match_the_generator_field_for_field(sapi: Api) -> None:
    got = {i["key"]: i for i in (await snapshot(sapi))["items"]}
    want = {i["key"]: i for i in SNAP["items"]}
    assert set(got) == set(want)
    bad = {
        (k, f): (got[k][f], want[k][f])
        for k in want
        for f in FIELDS
        if (sorted(got[k][f]) if f in ("tags", "risks") else got[k][f])
        != (sorted(want[k][f]) if f in ("tags", "risks") else want[k][f])
    }
    assert not bad, dict(list(bad.items())[:10])
    # stage age is "days since entered", measured at the snapshot's own as_of
    assert all(abs((got[k]["stage_age_days"] or 0) - want[k]["stage_age_days"]) <= 1 for k in want)


async def test_structure_matches_the_generator(sapi: Api) -> None:
    s = await snapshot(sapi)
    assert s["stages"] == SNAP["stages"] and s["stage_expected_days"] == SNAP["stage_expected_days"]
    assert {p["key"] for p in s["projects"]} == {p["key"] for p in SNAP["projects"]}
    assert {(p["key"], p["product"], p["methodology"], p["team"]) for p in s["projects"]} == {
        (p["key"], p["product"], p["methodology"], p["team"]) for p in SNAP["projects"]
    }
    assert {e["key"] for e in s["epics"]} == {e["key"] for e in SNAP["epics"]}
    assert {(m["name"], m["project"], m["due"]) for m in s["milestones"]} == {
        (m["name"], m["project"], m["due"]) for m in SNAP["milestones"]
    }
    assert {(r["key"], r["p"], r["i"], r["status"]) for r in s["risks"]} == {
        (r["key"], r["p"], r["i"], r["status"]) for r in SNAP["risks"]
    }
    assert {p["handle"] for p in s["people"]} >= {p["handle"] for p in SNAP["people"]}
    assert s["range"] == {"from": "2026-10-09", "to": "2026-10-23"} and s["as_of"].startswith(
        "2026-10-23T15:30"
    )
    assert (
        len(s["violations"]) == len(SNAP["violations"]) == 36 and s["habits"]
    )  # the Phase 8 engine ran at as_of
    # items are consistent with the schema they are published under
    validate(s, json.loads((ROOT / "docs/api/snapshot-schema.json").read_text()))


async def test_epic_links_and_hours_are_range_bounded(sapi: Api) -> None:
    s = await snapshot(sapi)
    by = {i["key"]: i for i in s["items"]}
    want = {i["key"]: i for i in SNAP["items"]}
    assert all(by[k]["epic"] == want[k]["epic"] for k in want)
    narrow = await snapshot(sapi, days=1)
    assert sum(i["hours_logged"] for i in narrow["items"]) <= sum(i["hours_logged"] for i in s["items"])


async def test_scopes_narrow_the_snapshot(sapi: Api) -> None:
    web = await snapshot(sapi, scope="project:WEB")
    assert {i["project"] for i in web["items"]} == {"WEB"} and web["meta"]["scope_label"].startswith(
        "Project:"
    )
    cust = await snapshot(sapi, scope="product:CUST")
    assert {i["project"] for i in cust["items"]} == {"WEB", "MOB"} and all(
        i["product"] == "CUST" for i in cust["items"]
    )
    sara = await snapshot(sapi, scope="person:@sara")
    assert {i["assignee"] for i in sara["items"]} == {"@sara"}
    team = await snapshot(sapi, scope="team:squad-b")
    assert team["items"] and len(team["items"]) < len(SNAP["items"])


def test_committed_schemas_are_in_sync_with_the_models() -> None:
    for name, text in render().items():
        assert (ROOT / "docs" / "api" / name).read_text() == text, (
            f"{name} is stale: run `uv run python tools/gen_schemas.py`"
        )


def test_the_prototype_generator_output_validates_against_the_snapshot_schema() -> None:
    golden = json.loads((ROOT / "apps/presentation/fixtures/golden.json").read_text())
    validate(golden["snapshot"], json.loads((ROOT / "docs/api/snapshot-schema.json").read_text()))


@pytest.mark.parametrize("fmt", ["pdf", "png"])
async def test_pdf_and_png_exports_render_the_real_bundle(sapi: Api, fmt: str) -> None:
    bundle = ROOT / "apps/presentation/dist/presentation.html"
    if not bundle.exists():
        pytest.skip("presentation bundle not built (pnpm --filter @tt/presentation build)")
    import io
    import re
    import zipfile

    p = (
        await sapi.call("POST", "/presentations:from-template", json={"template": "portfolio_review"})
    ).json()
    r = await sapi.call("POST", f"/presentations/{p['id']}/render", params={"format": fmt})
    assert r.status_code == 200, r.text
    if fmt == "pdf":
        assert r.content.startswith(b"%PDF") and len(re.findall(rb"/Type\s*/Page[^s]", r.content)) == 9
    else:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        pngs = [n for n in z.namelist() if n.endswith(".png")]
        assert len(pngs) == 9 and z.read(pngs[0]).startswith(b"\x89PNG") and "stamp.txt" in z.namelist()
