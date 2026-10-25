"""Plan P7 §7.6: generation pack (switch, redaction, stamping, event), import (strict/lenient, sanitising, provenance), author."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tt_server.db import models as m
from tt_server.domain import generation_pack as gen

from tests.api.conftest import Api
from tests.api.test_acl_llm import answer, fake_gateway

ROUTERS_COVERED = ("presentations",)


async def _enable(sf: async_sessionmaker[AsyncSession], **extra: Any) -> None:
    async with sf() as s:
        ws = await s.scalar(select(m.Workspace).where(m.Workspace.slug == "acme"))
        assert ws
        ws.settings = {**ws.settings, "external_packs": True, **extra}
        await s.commit()


async def test_pack_is_off_until_the_workspace_allows_it(api: Api) -> None:
    r = await api.call("POST", "/presentations:generation-pack", json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "external_packs_disabled"


async def test_pack_zip_has_the_documented_layout_and_a_checksum(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _enable(session_factory)
    await api.call("POST", "/items", json={"title": "one", "project": "WEB"})
    r = await api.call(
        "POST", "/presentations:generation-pack", json={"brief": "Q3 review", "scope": "project:WEB"}
    )
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {
        "manifest.json", "data/snapshot.json", "context/slide-kinds.json", "prompt/author.md",
        "template/deck.schema.json", "template/template.schema.json",
    } <= names  # fmt: skip
    assert any(n.startswith("template/examples/") for n in names)
    man = json.loads(z.read("manifest.json"))
    import hashlib

    assert man["snapshot_sha256"] == hashlib.sha256(z.read("data/snapshot.json")).hexdigest()
    assert man["generated_for"] == "@mahmoud" and man["redaction"] == "standard" and man["pack_version"] == 1
    prompt = z.read("prompt/author.md").decode()
    assert "Q3 review" in prompt and man["pack_id"] in prompt


async def test_pack_never_carries_wellbeing_or_live_sessions_and_strict_pseudonymises(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _enable(session_factory)
    std = (await api.call("POST", "/presentations:generation-pack", json={"format": "json"})).json()["files"]
    snap = json.loads(std["data/snapshot.json"])
    assert snap["habits"] == [] and snap["now"] == []
    strict = (
        await api.call(
            "POST", "/presentations:generation-pack", json={"format": "json", "redaction": "strict"}
        )
    ).json()
    assert "@mahmoud" not in strict["files"]["data/snapshot.json"]
    bad = await api.call("POST", "/presentations:generation-pack", json={"redaction": "nope"})
    assert bad.status_code == 400


async def test_pack_generation_is_an_event_and_needs_share_permission(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _enable(session_factory)
    await api.call("POST", "/presentations:generation-pack", json={})
    async with session_factory() as s:
        ev = (
            await s.scalars(select(m.Event).where(m.Event.event_type == "presentation.pack_generated"))
        ).all()
    assert len(ev) == 1
    # a plain member (no view.share) is refused
    r = await api.call("POST", "/presentations:generation-pack", as_="@nobody", json={})
    assert r.status_code in (401, 403, 404)


DECK = {
    "pack_id": "pk1",
    "title": "Review",
    "slides": [
        {"kind": "kpis", "title": "Numbers"},
        {
            "kind": "markdown",
            "config": {"text": "hello <script>alert(1)</script> [x](javascript:alert(1))"},
            "notes": "<iframe src=x></iframe>ok",
        },
    ],
}


async def test_import_keeps_provenance_and_sanitises_markdown(api: Api) -> None:
    r = await api.call("POST", "/presentations:import", json={"deck": DECK})
    assert r.status_code == 201, r.text
    got = (await api.call("GET", f"/presentations/{r.json()['id']}")).json()
    assert got["source"]["kind"] == "external_llm" and got["source"]["pack_id"] == "pk1"
    md = got["slides"][1]
    assert (
        "<script" not in md["config"]["text"]
        and "javascript:" not in md["config"]["text"]
        and "<iframe" not in md["notes"]
    )


async def test_import_is_strict_by_default_with_json_pointers(api: Api) -> None:
    bad = {
        "title": "T",
        "slides": [
            {"kind": "kpis"},
            {"kind": "hologram"},
            {"kind": "project_detail", "config": {"project_key": "NOPE"}},
        ],
    }
    r = await api.call("POST", "/presentations:import", json={"deck": bad})
    assert r.status_code == 422
    errs = r.json()["error"]["details"]["errors"]
    assert {e["pointer"] for e in errs} == {"/slides/1/kind", "/slides/2/config/project_key"}
    assert (await api.call("GET", "/presentations")).json()["data"] == []  # nothing was created


async def test_lenient_import_drops_bad_slides_but_reports_them(api: Api) -> None:
    bad = {"title": "T", "slides": [{"kind": "kpis"}, {"kind": "hologram"}]}
    r = await api.call("POST", "/presentations:import", json={"deck": bad, "lenient": True})
    assert (
        r.status_code == 201 and len(r.json()["slides"]) == 1 and "dropped slide 1" in r.json()["warnings"][0]
    )
    none = await api.call(
        "POST",
        "/presentations:import",
        json={"deck": {"title": "T", "slides": [{"kind": "x"}]}, "lenient": True},
    )
    assert none.status_code == 422


async def test_dry_run_validates_without_saving_and_hostile_inputs_are_rejected(api: Api) -> None:
    ok = await api.call("POST", "/presentations:import", json={"deck": DECK, "dry_run": True})
    assert ok.status_code == 200 and ok.json()["valid"] is True
    assert (await api.call("GET", "/presentations")).json()["data"] == []
    for deck in (
        {"title": "T", "slides": "nope"},
        {"title": "", "slides": [{"kind": "kpis"}]},
        {"title": "T", "slides": [{"kind": "image", "config": {"url": "http://evil.example/x.png"}}]},
        {"title": "T", "slides": [{"kind": "kpis"}] * 61},
        {"title": "T", "slides": [None]},
    ):
        r = await api.call("POST", "/presentations:import", json={"deck": deck})
        assert r.status_code == 422, deck


def test_sanitiser_unit() -> None:
    assert gen.sanitise_text("<img src=x onerror=alert(1)> ok") == "<img src=x> ok"
    assert gen.sanitise_text("[a](data:text/html;base64,xx)") == "[a](#)"
    assert gen.sanitise_text(None) is None


async def test_author_drafts_through_the_gateway_and_records_the_run(api: Api) -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return answer(
            {
                "title": "ERP review",
                "slides": [{"kind": "roadmap", "title": "Plan", "config": {"project_key": "WEB"}}],
            }
        )

    api.c._transport.app.state.llm = fake_gateway(handler)  # type: ignore[attr-defined]
    r = await api.call("POST", "/presentations:author", json={"brief": "a quarterly review"})
    assert r.status_code == 201, r.text
    assert seen[0]["request"] == "a quarterly review" and seen[0]["pack"]["slide_kinds"]
    assert r.json()["source"]["kind"] == "gateway_llm" and r.json()["source"]["run_id"]
    api.c._transport.app.state.llm = None  # type: ignore[attr-defined]
    assert (await api.call("POST", "/presentations:author", json={"brief": "x y z"})).status_code == 503


async def test_author_drops_slides_for_projects_the_user_cannot_read(api: Api) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return answer(
            {
                "title": "T",
                "slides": [{"kind": "kpis"}, {"kind": "roadmap", "config": {"project_key": "SECRET"}}],
            }
        )

    api.c._transport.app.state.llm = fake_gateway(handler)  # type: ignore[attr-defined]
    r = await api.call("POST", "/presentations:author", json={"brief": "anything"})
    assert r.status_code == 201 and [s["kind"] for s in r.json()["slides"]] == ["kpis"]
    api.c._transport.app.state.llm = None  # type: ignore[attr-defined]


async def test_echo_round_trip_pack_to_import(
    api: Api, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Contract test: an 'echo model' that reads only the pack's catalogue and snapshot produces a deck the import accepts."""
    await _enable(session_factory)
    await api.call("POST", "/items", json={"title": "one", "project": "WEB"})
    files = (await api.call("POST", "/presentations:generation-pack", json={"format": "json"})).json()[
        "files"
    ]
    kinds = json.loads(files["context/slide-kinds.json"])
    snap = json.loads(files["data/snapshot.json"])
    pack_id = json.loads(files["manifest.json"])["pack_id"]
    slides: list[dict[str, Any]] = [
        {"kind": k["kind"], "config": {}} for k in kinds if not k["config_schema"]["required"]
    ]
    for p in snap.get("projects", [])[:1]:
        slides.append({"kind": "roadmap", "config": {"project_key": p["key"]}})
    deck = {"pack_id": pack_id, "title": "Echo", "slides": slides}
    jsonschema_keys = set(json.loads(files["template/deck.schema.json"])["properties"])
    assert {"title", "slides", "pack_id"} <= jsonschema_keys
    r = await api.call("POST", "/presentations:import", json={"deck": deck})
    assert r.status_code == 201, r.text
    assert r.json()["source"]["pack_id"] == pack_id
