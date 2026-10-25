"""Plan P7 §7.5: presentation templates are data - CRUD, validate, preview, export/import, duplicate, share, save-as-template."""

from __future__ import annotations

import json
from typing import Any

from tt_server.domain import presentation_templates as tpl

from tests.api.conftest import Api

ROUTERS_COVERED = ("presentation_templates",)

BODY: dict[str, Any] = {
    "schema_version": 1, "key": "weekly_status", "name": "Weekly status: ${project}", "description": "mine",
    "params": [{"name": "project", "type": "project", "required": True}, {"name": "horizon", "type": "int", "default": 7}],
    "slides": [
        {"kind": "title", "title_template": "${project.name} weekly", "config": {}},
        {"kind": "milestones", "title_template": "Milestones for ${project}", "config": {"project_key": "${project}"}},
        {"kind": "radar", "title_template": "Next ${horizon} days", "config": {"days": "${horizon}"}, "requires": "activity", "render_empty": "note"},
    ],
    "post_rules": [{"when": "scope.items == 0", "action": "drop_slides", "kinds": ["radar"]}],
}  # fmt: skip


async def test_the_builtin_templates_are_rows_loaded_from_the_shipped_files(api: Api) -> None:
    got = (await api.call("GET", "/presentation-templates")).json()["data"]
    keys = {t["key"] for t in got}
    assert {"portfolio_review", "project_plan_review", "programme_roadmap"} <= keys and all(
        t["is_builtin"] for t in got
    )
    again = (await api.call("GET", "/presentation-templates")).json()["data"]
    assert len(again) == len(got)  # idempotent upsert
    assert {t["key"] for t in (await api.call("GET", "/presentations/templates")).json()["data"]} == keys


async def test_slide_kinds_publish_their_config_schemas_and_the_plan_only_rule(api: Api) -> None:
    kinds = {k["kind"]: k for k in (await api.call("GET", "/presentation/slide-kinds")).json()["data"]}
    assert kinds["roadmap"]["requires"] == "plan" and kinds["kpis"]["render_empty"] == "note"
    assert kinds["explorer"]["config_schema"]["properties"]["measure"]["enum"]
    assert "project_key" in kinds["deliverables"]["config_schema"]["properties"]


async def test_validate_reports_json_pointers(api: Api) -> None:
    ok = (await api.call("POST", "/presentation-templates:validate", json={"body": BODY})).json()
    assert ok == {"valid": True, "errors": []}
    bad = {
        **BODY,
        "slides": [
            {"kind": "nope", "config": {}},
            {"kind": "markdown", "config": {"text": "${ghost}"}},
            {"kind": "view", "config": {}},
        ],
        "key": "Bad Key",
    }
    r = (await api.call("POST", "/presentation-templates:validate", json={"body": bad})).json()
    assert r["valid"] is False
    pointers = {e["pointer"] for e in r["errors"]}
    assert "/key" in pointers
    only_slides = (
        await api.call("POST", "/presentation-templates:validate", json={"body": {**bad, "key": "ok_key"}})
    ).json()["errors"]
    pointers = {e["pointer"] for e in only_slides}
    assert (
        "/slides/0/kind" in pointers
        and any(p.startswith("/slides/1") for p in pointers)
        and any(p.startswith("/slides/2") for p in pointers)
    )
    dup = {**BODY, "params": [{"name": "a"}, {"name": "a"}]}
    assert any(
        e["pointer"] == "/params/1/name"
        for e in (await api.call("POST", "/presentation-templates:validate", json={"body": dup})).json()[
            "errors"
        ]
    )


async def test_create_edit_instantiate_with_copy_semantics(api: Api) -> None:
    r = await api.call("POST", "/presentation-templates", json={"body": BODY})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["version"] == 1 and not t["is_builtin"] and t["slides"] == ["title", "milestones", "radar"]
    assert (await api.call("POST", "/presentation-templates", json={"body": BODY})).status_code == 409
    await api.send(action="create", item={"title": "keeps radar alive"}, project="WEB")
    missing = await api.call(
        "POST", "/presentations:from-template", json={"template": "weekly_status", "scope": "project:WEB"}
    )
    assert missing.status_code == 400 and missing.json()["error"]["pointer"] == "/params/project"
    deck = await api.call(
        "POST",
        "/presentations:from-template",
        json={
            "template": "weekly_status",
            "scope": "project:WEB",
            "params": {"project": "WEB", "horizon": 3},
        },
    )
    assert deck.status_code == 201, deck.text
    d = deck.json()
    assert d["title"] == "Weekly status: WEB" and [s["title"] for s in d["slides"]][1:] == [
        "Milestones for WEB",
        "Next 3 days",
    ]
    assert d["slides"][2]["config"]["days"] == 3  # a lone placeholder keeps its type
    # editing the template never changes the deck made from it
    edited = {**BODY, "slides": [BODY["slides"][0]]}
    up = await api.call("PUT", f"/presentation-templates/{t['id']}", json={"body": edited})
    assert up.status_code == 200 and up.json()["version"] == 2
    same = (await api.call("GET", f"/presentations/{d['id']}")).json()
    assert len(same["slides"]) == 3


async def test_post_rules_drop_slides_for_an_empty_scope(api: Api) -> None:
    await api.call("POST", "/presentation-templates", json={"body": BODY})
    p = (
        await api.call(
            "POST",
            "/presentation-templates/weekly_status:preview",
            json={"scope": "project:WEB", "params": {"project": "WEB"}},
        )
    ).json()
    assert [s["kind"] for s in p["slides"]] == ["title", "milestones"] and p["stats"]["scope.items"] == 0
    await api.send(action="create", item={"title": "something"}, project="WEB")
    p2 = (
        await api.call(
            "POST",
            "/presentation-templates/weekly_status:preview",
            json={"scope": "project:WEB", "params": {"project": "WEB"}},
        )
    ).json()
    assert [s["kind"] for s in p2["slides"]] == ["title", "milestones", "radar"]
    draft = {**BODY, "slides": [{"kind": "markdown", "config": {"text": "hi ${today}"}}]}
    pv = (
        await api.call(
            "POST",
            "/presentation-templates/weekly_status:preview",
            json={"body": draft, "params": {"project": "WEB"}},
        )
    ).json()
    assert pv["slides"][0]["config"]["text"].startswith("hi 20")


async def test_export_import_duplicate_share_and_builtins_are_read_only(api: Api) -> None:
    await api.call("POST", "/presentation-templates", json={"body": BODY})
    exp = await api.call("GET", "/presentation-templates/weekly_status:export")
    assert exp.status_code == 200 and "tt-template.json" in exp.headers["content-disposition"]
    doc = json.loads(exp.text)
    doc["key"] = "imported_one"
    imp = await api.call("POST", "/presentation-templates:import", json={"body": doc})
    assert imp.status_code == 201 and imp.json()["key"] == "imported_one"
    doc["slides"].append({"kind": "hologram", "config": {}})
    assert (
        await api.call("POST", "/presentation-templates:import", json={"body": {**doc, "key": "other_key"}})
    ).status_code == 400
    dup = await api.call("POST", "/presentation-templates/portfolio_review:duplicate")
    assert (
        dup.status_code == 201
        and dup.json()["key"] == "portfolio_review_copy"
        and not dup.json()["is_builtin"]
    )
    assert (
        await api.call("PUT", "/presentation-templates/portfolio_review", json={"body": BODY})
    ).status_code == 403
    assert (await api.call("DELETE", "/presentation-templates/portfolio_review")).status_code == 403
    # private until shared
    assert "weekly_status" not in {
        t["key"] for t in (await api.call("GET", "/presentation-templates", as_="@sara")).json()["data"]
    }
    await api.role("@sara", "manager")
    sh = await api.call("POST", "/presentation-templates/weekly_status:share")
    assert sh.status_code == 200 and sh.json()["is_shared"]
    assert "weekly_status" in {
        t["key"] for t in (await api.call("GET", "/presentation-templates", as_="@sara")).json()["data"]
    }
    assert (
        await api.call("PUT", "/presentation-templates/weekly_status", as_="@sara", json={"body": BODY})
    ).status_code == 403
    assert (await api.call("DELETE", "/presentation-templates/weekly_status")).status_code == 204


async def test_save_a_presentation_as_a_template_lifts_values_into_parameters(api: Api) -> None:
    deck = (
        await api.call(
            "POST",
            "/presentations",
            json={
                "title": "WEB review",
                "slides": [{"kind": "title"}, {"kind": "milestones", "config": {"project_key": "WEB"}}],
            },
        )
    ).json()
    r = await api.call(
        "POST",
        f"/presentations/{deck['id']}:save-as-template",
        json={"name": "Project review", "params_to_extract": ["WEB"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()["body"]
    assert body["params"] == [
        {"name": "p1", "type": "string", "required": False, "default": "WEB", "label": None}
    ]
    assert body["slides"][1]["config"]["project_key"] == "${p1}"
    again = await api.call(
        "POST", "/presentations:from-template", json={"template": "project_review", "params": {"p1": "ERP"}}
    )
    assert again.status_code == 201 and again.json()["slides"][1]["config"]["project_key"] == "ERP"


def test_the_schema_document_matches_the_model() -> None:
    from pathlib import Path

    doc = json.loads(
        (Path(__file__).resolve().parents[4] / "docs/api/presentation-template-schema.json").read_text()
    )
    assert doc == json.loads(json.dumps(tpl.json_schema(), sort_keys=True))
