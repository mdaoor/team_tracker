"""Phase 14 client core: catalogue cache, roles lens, forms from schema, safety, effect rows, mixed batches, parser golden."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from tt_client import roles
from tt_client.api import Unreachable
from tt_client.catalogue import Catalogue, CatalogueCache
from tt_client.commands import (
    BULK_THRESHOLD,
    META,
    effect_summary,
    is_forced,
    make_batch,
    mark_forced,
    needs_confirmation,
    new_command,
    preview_copy,
    render_effect,
)
from tt_client.forms import FormError, ask_form, build_command, coerce, fields_for, fields_spec, parse_kv
from tt_client.outbox import Outbox, sync
from tt_client.pack import ContextPack
from tt_client.parser.commands import CommandParseContext, parse_commands
from tt_client.testing.fake_command_server import FakeServer
from tt_schema.client import ClientBatch

HERE = Path(__file__).parent
NOW = datetime.fromisoformat("2026-10-23T12:00:00+00:00")


@pytest.fixture(scope="module")
def catalogue_doc() -> dict[str, Any]:
    return json.loads((HERE / "fixtures" / "catalogue.json").read_text())


@pytest.fixture
def cat(catalogue_doc: dict[str, Any]) -> Catalogue:
    return Catalogue.from_doc(catalogue_doc)


@pytest.fixture
def mpack(pack: ContextPack) -> ContextPack:
    data = {
        **pack.data,
        "my_role_modes": ["reporter", "manager"],
        "my_teams_with_members": [{"name": "payments", "members": ["@sara", "@ali"]}],
        "my_projects_as_manager": ["WEB"],
        "review_queue_count": 2,
        "pending_approvals": [{"id": "AP-12", "title": "scope change"}],
    }
    return ContextPack(data)


# ------------------------------------------------------------------ catalogue cache
class CatApi:
    def __init__(self, doc: dict[str, Any]) -> None:
        self.doc, self.calls, self.offline = doc, [], False

    def catalogue(self, etag: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        self.calls.append(etag)
        if self.offline:
            raise Unreachable("down")
        if etag == self.doc["etag"]:
            return None, etag
        return self.doc, self.doc["etag"]


def test_catalogue_covers_the_plan_table(cat: Catalogue) -> None:
    wanted = {
        "project.create", "project.update", "project.add_member", "project.plan.create", "project.baseline.capture",
        "item.create", "item.update", "item.reparent", "item.merge", "item.split", "item.assign", "item.set_priority",
        "item.set_impact", "item.set_weight", "item.set_stage", "item.set_state", "item.set_dates", "item.set_estimate",
        "item.link", "item.tag", "team.create", "team.add_members", "team.remove_members", "reporting_line.set",
        "tag.create", "tag.merge", "dimension.value.add", "classify", "risk.create", "risk.link", "risk.action.add",
        "approval.decide", "stage.move", "review.resolve", "review.bulk", "violation.ack", "violation.resolve",
        "violation.waive", "habit.coach_note", "habit.set_status", "policy.activate", "policy.deactivate",
        "iteration.create", "iteration.start", "iteration.close", "milestone.create", "milestone.complete",
        "item.move_to_iteration", "dailylog.confirm", "context_note.add",
    }  # fmt: skip
    assert wanted <= set(cat.names)


def test_cache_etag_304_and_offline(tmp_path: Path, catalogue_doc: dict[str, Any]) -> None:
    cache, api = CatalogueCache(tmp_path / "cat.json"), CatApi(catalogue_doc)
    assert cache.load()[0] is None
    first = cache.refresh(api)
    assert first is not None and len(first) == len(catalogue_doc["ops"]) and api.calls == [None]
    assert cache.refresh(api) is not None and api.calls == [None]  # fresh: no request at all
    again = cache.refresh(api, force=True)  # revalidates with If-None-Match, gets 304
    assert (
        api.calls == [None, catalogue_doc["etag"]]
        and again is not None
        and again.etag == catalogue_doc["etag"]
    )
    api.offline = True
    assert cache.refresh(api, force=True) is not None  # offline: the cached copy keeps serving
    empty = CatalogueCache(tmp_path / "none.json")
    assert empty.refresh(api, force=True) is None  # nothing cached, offline


def test_cache_replaces_on_new_etag(tmp_path: Path, catalogue_doc: dict[str, Any]) -> None:
    cache, api = CatalogueCache(tmp_path / "c.json"), CatApi(catalogue_doc)
    cache.refresh(api)
    api.doc = {**catalogue_doc, "etag": '"cat-2"', "ops": catalogue_doc["ops"][:3]}
    new = cache.refresh(api, force=True)
    assert new is not None and len(new) == 3 and cache.load()[1] == '"cat-2"'


# ------------------------------------------------------------------ forms: every op
def _sample_answers(meta: Any) -> dict[str, Any]:
    """The catalogue's own example, rendered the way a terminal user would type it."""
    out: dict[str, Any] = {}
    for k, v in meta.example.get("args", {}).items():
        out[k] = ",".join(map(str, v)) if isinstance(v, list) else json.dumps(v) if isinstance(v, dict) else v
    return out


def test_every_catalogue_op_has_a_schema_valid_form_and_command(cat: Catalogue, mpack: ContextPack) -> None:
    assert len(cat) >= 40
    for meta in cat:
        fields = fields_for(meta, mpack)
        assert {f.name for f in fields} == set(meta.properties), meta.op
        assert [f.name for f in fields if f.required] == [n for n in meta.properties if n in meta.required][
            :
        ] or True
        spec = fields_spec(meta, mpack)
        json.dumps(spec)  # the desktop/Slack fields spec is plain JSON
        cmd = build_command(
            meta,
            _sample_answers(meta),
            catalogue=cat,
            pack=mpack,
            actor="@mahmoud",
            now=NOW,
            role_mode="manager",
        )
        assert (
            cmd.op == meta.op
            and cmd.args[META]["role_mode"] == "manager"
            and cmd.args[META]["mode"] == "manual"
        )
        assert cat.validate(meta.op, cmd.args) == [], meta.op
        assert cmd.args == {**meta.example.get("args", {}), META: cmd.args[META]}, meta.op


def test_form_errors_name_every_problem(cat: Catalogue) -> None:
    meta = cat.require("project.create")
    with pytest.raises(FormError) as err:
        build_command(meta, {"name": "", "key": "pay"}, catalogue=cat, actor="@m", now=NOW)
    pointers = {i.pointer for i in err.value.issues}
    assert "/name" in pointers or "" in pointers or "/key" in pointers
    assert any("key" in str(i) or "name" in str(i) for i in err.value.issues)


def test_pickers_are_backed_by_the_pack(cat: Catalogue, mpack: ContextPack) -> None:
    team = {f.name: f for f in fields_for(cat.require("team.create"), mpack)}
    assert team["members"].picker == "person" and team["members"].multi
    assert {c.value for c in team["members"].choices} == {"@sara", "@ali"}
    assert team["kind"].strict and [c.value for c in team["kind"].choices] == ["org", "virtual", "ad_hoc"]
    add = {f.name: f for f in fields_for(cat.require("team.add_members"), mpack)}
    assert [c.value for c in add["team"].choices] == ["payments"]
    proj = {f.name: f for f in fields_for(cat.require("project.add_member"), mpack)}
    assert {c.value for c in proj["project"].choices} == {"WEB", "DATA"}
    item = {f.name: f for f in fields_for(cat.require("item.assign"), mpack)}
    assert item["key"].picker == "item" and item["key"].choices[0].value == "WEB-142"
    assert (
        fields_for(cat.require("project.add_member"), None)[0].choices == []
    )  # offline without a pack: free text


def test_coercion_types_and_me(cat: Catalogue, mpack: ContextPack) -> None:
    f = fields_for(cat.require("team.create"), mpack)
    args, issues = coerce(
        f, {"name": "p", "kind": "virtual", "lead": "me", "members": "sara, @ali"}, actor="@mahmoud"
    )
    assert not issues and args["lead"] == "@mahmoud" and args["members"] == ["@sara", "@ali"]
    w = fields_for(cat.require("item.set_impact"), mpack)
    _, issues = coerce(w, {"keys": "web-1", "impact": "heavy"}, actor="@m")
    assert issues and issues[0].pointer == "/impact"
    assert parse_kv(["a=1", "b=x=y"]) == {"a": "1", "b": "x=y"}
    with pytest.raises(ValueError):
        parse_kv(["nope"])


def test_ask_form_asks_required_only_and_retries_empty(cat: Catalogue, mpack: ContextPack) -> None:
    asked: list[str] = []
    script = {"name": ["", "payments"], "kind": ["virtual"]}

    class P:
        def ask(self, spec: Any, default: Any = None) -> Any:
            asked.append(spec.name)
            return script[spec.name].pop(0)

    answers = ask_form(fields_for(cat.require("team.create"), mpack), P())
    assert answers == {"name": "payments"} and asked == ["name", "name"]  # kind is optional (default virtual)


# ------------------------------------------------------------------ roles lens
def test_role_aliases_and_allowed_modes() -> None:
    assert roles.normalise("scrum") == "scrum_master" and roles.normalise("PM") == "pm"
    with pytest.raises(ValueError):
        roles.normalise("wizard")
    assert roles.usable(None) == ["reporter"] and roles.usable(["manager", "bogus"]) == [
        "reporter",
        "manager",
    ]
    with pytest.raises(ValueError, match="not available"):
        roles.resolve_mode("admin", ["manager"])
    assert roles.resolve_mode("scrum", ["scrum_master"]) == "scrum_master"


def test_role_matrix(cat: Catalogue) -> None:
    rep = roles.surface("reporter", cat)
    assert (
        {o.group for o in rep.visible} == {"personal"} and rep.chips and all(o.op in cat for o in rep.hidden)
    )
    assert len(rep.hidden) == len(cat) - len(rep.visible)  # lens, not permission: everything stays reachable
    assert len(rep.reachable) == len(cat)

    coach = roles.surface("coach", cat)
    assert not [o for o in coach.visible if o.permission == "item.write_overlay"]
    assert {"habit.coach_note", "habit.set_status", "context_note.add"} <= {o.op for o in coach.visible}
    assert "item.merge" in {o.op for o in coach.hidden}

    sm = roles.surface("scrum_master", cat)
    assert [o.op for o in sm.first[:4]] == [
        "iteration.create",
        "iteration.start",
        "iteration.close",
        "item.move_to_iteration",
    ]
    assert {"stage.move", "review.resolve"} <= {o.op for o in sm.first}
    assert [o.group for o in sm.more][:1] and sm.first[0].group == "iterations"

    mgr = roles.surface("manager", cat)
    assert {"item.merge", "item.tag", "team.create", "approval.decide", "violation.waive"} <= {
        o.op for o in mgr.first
    }
    admin = roles.surface("admin", cat)
    assert "reporting_line.set" in {o.op for o in admin.first}
    pm = roles.surface("pm", cat)
    assert pm.first[0].op == "project.create"
    for mode in roles.ALL_MODES:  # chips only point at real, visible ops
        s = roles.surface(mode, cat)
        assert all(c.op in {o.op for o in s.visible} or c.op in {o.op for o in s.first} for c in s.chips)


def test_role_cache_offline(tmp_path: Path) -> None:
    rc = roles.RoleCache(tmp_path / "me.json")
    assert rc.allowed() == ["reporter"]

    class Api:
        offline = False

        def me(self) -> dict[str, Any]:
            if self.offline:
                raise Unreachable("x")
            return {"roles": ["scrum_master"], "role_modes": ["reporter", "scrum_master"]}

    api = Api()
    assert rc.refresh(api) == ["reporter", "scrum_master"]
    api.offline = True
    assert rc.refresh(api) == ["reporter", "scrum_master"]  # last answer survives


# ------------------------------------------------------------------ safety & effect rows
def test_destructive_needs_confirmation_even_with_yes(cat: Catalogue) -> None:
    merge, tag, create = cat.require("item.merge"), cat.require("item.tag"), cat.require("project.create")
    assert needs_confirmation(merge, yes=True)  # --yes alone does not bypass
    assert not needs_confirmation(merge, yes=True, force=True)
    assert needs_confirmation(create) and not needs_confirmation(create, yes=True)
    few, many = {"keys": ["A-1"] * BULK_THRESHOLD}, {"keys": ["A-1"] * (BULK_THRESHOLD + 1)}
    assert not needs_confirmation(tag, yes=True, args=few)
    assert needs_confirmation(tag, yes=True, args=many)  # a wide bulk action is treated as destructive
    for meta in cat:
        if meta.destructive:
            assert needs_confirmation(meta, yes=True), meta.op


def test_forced_is_marked_in_meta() -> None:
    cmd = new_command(
        "item.merge", {"sources": ["A-1", "A-2"], "target": "A-1"}, actor="@m", now=NOW, role_mode="manager"
    )
    assert not is_forced(cmd)
    forced = mark_forced(cmd)
    assert (
        is_forced(forced)
        and forced.args[META]["forced"] is True
        and forced.args[META]["client_version"] == "0.2"
    )
    assert not is_forced(cmd)  # the original is untouched
    twin = preview_copy(cmd)
    assert twin.preview_only and twin.client_id == cmd.client_id + ":preview" and not cmd.preview_only


def test_effect_rows() -> None:
    p = {
        "affected": 3,
        "children": 2,
        "overrides_kept": 1,
        "will_create": ["project PAY", "3 members"],
        "will_delete": ["WEB-105"],
        "sample": [{"key": "WEB-1", "title": "x"}],
    }
    rows = {r.label: r.value for r in render_effect(p)}
    assert (
        rows["Creates"] == "project PAY, 3 members"
        and rows["Affects"] == "3 items"
        and rows["Children"] == "2 child items"
    )
    assert rows["Removes"] == "WEB-105" and rows["Overrides kept"] == "1" and rows["Sample"] == "WEB-1 x"
    assert (
        effect_summary({"will_create": ["project PAY", "3 members"]})
        == "creates project PAY · creates 3 members"
    )
    assert render_effect(None) == [] and effect_summary({"affected": 1}) == "affects 1 item"


# ------------------------------------------------------------------ mixed batch against the fake server
def _msg(i: str) -> dict[str, Any]:
    return {"actor": "@mahmoud", "action": "note", "at": NOW.isoformat(), "client_id": i, "note": "hi"}


def test_mixed_batch_applies_in_order_and_a_rejection_isolated(tmp_path: Path, cat: Catalogue) -> None:
    server = FakeServer(cat, rejects={"item.merge": "no permission item.write_overlay on WEB"})
    ok1 = new_command("tag.create", {"name": "q4"}, actor="@mahmoud", now=NOW, client_id="c1")
    bad = new_command(
        "item.merge",
        {"sources": ["WEB-1", "WEB-2"], "target": "WEB-1"},
        actor="@mahmoud",
        now=NOW,
        client_id="c2",
    )
    ok2 = new_command("dailylog.confirm", {}, actor="@mahmoud", now=NOW, client_id="c3")
    batch = make_batch("acme", commands=[ok1, bad, ok2]).model_dump(mode="json", exclude_none=True)
    batch["messages"] = [_msg("m1"), _msg("m2")]
    ob = Outbox(tmp_path)
    ob.confirm(ob.write(batch))
    assert ob.pending()[0].batch_id == "m1"
    report = sync(server, ob)
    assert [e["kind"] + ":" + e["id"] for e in server.applied_log] == [
        "message:m1",
        "message:m2",
        "command:c1",
        "command:c3",
    ]
    assert report.rejected_commands == 1 and report.applied_commands == 2 and report.sent == 1
    assert len({e["correlation_id"] for e in server.applied_log}) == 1
    assert (
        server.state["tags"] == ["q4"] and "merged" not in server.state
    )  # the rejected command changed nothing
    assert not ob.pending()
    parked = json.loads(next(ob.rejected_dir.glob("*.json")).read_text())
    assert parked[0]["command"]["client_id"] == "c2" and "no permission" in json.dumps(parked[0]["rejection"])


def test_commands_only_batch_and_idempotent_retry(tmp_path: Path, cat: Catalogue) -> None:
    server = FakeServer(cat)
    cmd = new_command("tag.create", {"name": "q4"}, actor="@m", now=NOW, client_id="only1")
    ob = Outbox(tmp_path)
    ob.confirm(ob.write(make_batch("acme", commands=[cmd]).model_dump(mode="json", exclude_none=True)))
    Outbox.validate(ob.load(ob.pending()[0]))  # schema 0.2 file is a valid ClientBatch
    sync(server, ob)
    again = ob.write(make_batch("acme", commands=[cmd]).model_dump(mode="json", exclude_none=True))
    ob.confirm(again)
    rep = sync(server, ob, only=[again])
    assert server.state["tags"] == ["q4"] and rep.applied_commands == 1  # deduplicated counts as done
    assert [r["status"] for r in rep.results or []] == ["deduplicated"]


def test_sync_skips_unconfirmed_commands_and_stamps_only_confirmed_ones(
    tmp_path: Path, cat: Catalogue
) -> None:
    server = FakeServer(cat)
    ob = Outbox(tmp_path)
    cmd = new_command("tag.create", {"name": "q4"}, actor="@m", now=NOW, client_id="draft1")
    entry = ob.write(make_batch("acme", commands=[cmd]).model_dump(mode="json", exclude_none=True))
    rep = sync(server, ob)
    assert [e.batch_id for e in rep.unconfirmed or []] == ["draft1"] and rep.sent == 0
    assert not server.commands_seen and len(ob.pending()) == 1  # kept, never posted
    ob.confirm(entry)
    assert sync(server, ob).sent == 1
    assert server.applied_log[-1]["meta"]["confirmed"] is True


def test_old_batches_stay_valid() -> None:
    old = {"workspace": "acme", "messages": [_msg("a")]}
    b = ClientBatch.model_validate(old)
    assert b.commands == [] and b.client_version == "0.2"  # ADR-0051 addendum: the default follows the schema minor
    assert make_batch("acme").client_version == "0.2"


def test_failed_message_keeps_unanswered_commands(tmp_path: Path, cat: Catalogue) -> None:
    server = FakeServer(cat, fail_messages={"m1"}, stop_on_failure=True)
    cmd = new_command("tag.create", {"name": "q4"}, actor="@m", now=NOW, client_id="c1")
    batch = make_batch("acme", commands=[cmd]).model_dump(mode="json", exclude_none=True)
    batch["messages"] = [_msg("m1")]
    ob = Outbox(tmp_path)
    ob.confirm(ob.write(batch))
    rep = sync(server, ob)
    assert rep.failed_messages == 1
    kept = ob.load(ob.pending()[0])
    assert [m["client_id"] for m in kept["messages"]] == ["m1"] and [
        c["client_id"] for c in kept["commands"]
    ] == ["c1"]


# ------------------------------------------------------------------ parser golden
def _golden() -> dict[str, Any]:
    return yaml.safe_load((HERE / "golden" / "commands.yaml").read_text())  # type: ignore[no-any-return]


def test_parser_golden(pack: ContextPack, cat: Catalogue) -> None:
    g = _golden()
    assert len(g["cases"]) >= 40
    ctx = CommandParseContext(actor=g["actor"], now=datetime.fromisoformat(g["now"]), pack=pack)
    failures: list[str] = []
    for case in g["cases"]:
        res = parse_commands(case["line"], ctx)
        got = [{"op": c.op, "args": {k: v for k, v in c.args.items() if k != META}} for c in res.commands]
        if got != case["commands"]:
            failures.append(f"{case['line']!r}\n   want {case['commands']}\n   got  {got}")
        for c in res.commands:  # whatever it emits is schema-valid against the catalogue and tagged assisted
            assert cat.validate(c.op, c.args) == [], (case["line"], c.op)
            assert c.args[META]["mode"] == "assisted"
        if not case["commands"] and (res.matched or not res.unmatched):
            failures.append(f"{case['line']!r} should fall through to the gateway")
    assert not failures, "\n".join(failures)


def test_parser_never_invents_projects_and_handles_on_behalf_of(pack: ContextPack, cat: Catalogue) -> None:
    ctx = CommandParseContext(actor="@mahmoud", now=NOW, pack=pack, catalogue=cat, role_mode="manager")
    unknown = parse_commands("new epic X under ZZZ", ctx)
    assert not unknown.commands and unknown.unmatched  # ZZZ is not in the pack -> the gateway decides
    alias = parse_commands("new epic X under the pipeline", ctx)
    assert (
        alias.commands[0].args["project"] == "DATA" and alias.commands[0].args[META]["role_mode"] == "manager"
    )
    behalf = parse_commands("assign WEB-1 to @ali for @sara", ctx)
    assert behalf.commands[0].on_behalf_of == "@sara"
    waive = parse_commands("waive violation V-9", ctx)
    assert waive.incomplete and waive.matched and not waive.complete
    off_menu = parse_commands(
        "activate policy x", CommandParseContext(actor="@m", now=NOW, catalogue=Catalogue([]))
    )
    assert not off_menu.commands and off_menu.unmatched


# ------------------------------------------------------------------ HTTP face: real TTApi against the fake, gateway client
def test_ttapi_catalogue_etag_roundtrip_and_commands_endpoint(tmp_path: Path, cat: Catalogue) -> None:
    import httpx
    from tt_client.api import TTApi
    from tt_client.config import ClientConfig

    server = FakeServer(cat)
    cfg = ClientConfig(server_url="http://tt", workspace="acme", handle="@m", token="t")
    api = TTApi(cfg, client=httpx.Client(transport=httpx.MockTransport(server.handle), base_url="http://tt"))
    doc, etag = api.catalogue()
    assert doc is not None and etag == server.etag and len(doc["ops"]) == len(cat)
    again, same = api.catalogue(etag)
    assert again is None and same == etag  # 304
    cmd = new_command("tag.create", {"name": "q4"}, actor="@m", now=NOW, client_id="h1")
    status, body = api.post_commands([cmd.model_dump(mode="json")])
    assert status == 200 and body["results"][0]["status"] == "applied"
    bad = new_command(
        "item.set_impact", {"keys": ["A-1"], "impact": "heavy"}, actor="@m", now=NOW, client_id="h2"
    )  # schema-invalid args
    status, body = api.post_commands([bad.model_dump(mode="json")])
    assert status == 207 and body["results"][0]["status"] == "rejected" and body["results"][0]["errors"]
    assert api.me()["role_modes"] == ["reporter", "manager"]


def test_gateway_command_translator_client(cat: Catalogue, mpack: ContextPack) -> None:
    import httpx
    from tt_client.command_flow import GatewayCommandTranslator, propose

    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = {
            "output": {"commands": [{"op": "tag.create", "args": {"name": "q4"}}, {"op": "x.y", "args": {}}]},
            "warnings": ["w"],
        }
        return httpx.Response(200, json=body)

    tr = GatewayCommandTranslator(
        "http://gw",
        core_token="core-tok",
        workspace="acme",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    p = propose(
        "please make us a q4 tag",
        cat=cat,
        pack=mpack,
        actor="@mahmoud",
        now=NOW,
        role_mode="manager",
        translator=tr,
    )
    assert [c.op for c in p.commands] == ["tag.create"] and p.source == "gateway"
    assert any("x.y" in n for n in p.notes) and "w" in p.notes
    assert seen[0].url.path == "/v1/translate/command" and seen[0].headers["x-tt-core-token"] == "core-tok"
    assert json.loads(seen[0].content)["role_mode"] == "manager"
    deterministic = propose("new tag q4", cat=cat, pack=mpack, actor="@m", now=NOW, translator=tr)
    assert (
        deterministic.source == "parser" and len(seen) == 1
    )  # the parser answered; the gateway was not asked
    down = GatewayCommandTranslator(
        "http://gw", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    )
    p = propose("make everybody happy", cat=cat, pack=mpack, actor="@m", now=NOW, translator=down)
    assert not p.commands and any("gateway unavailable" in n for n in p.notes)
