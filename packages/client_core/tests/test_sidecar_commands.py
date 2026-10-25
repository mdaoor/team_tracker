"""The desktop/mobile sidecar verbs for commands (ADR-0038): propose, fields, build, preview, send with enforced confirmation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from tt_client import roles
from tt_client.catalogue import Catalogue, CatalogueCache
from tt_client.outbox import Outbox
from tt_client.pack import ContextPack
from tt_client.sidecar_commands import VERBS, CommandContext, dispatch
from tt_client.testing.fake_command_server import FakeServer

NOW = datetime.fromisoformat("2026-10-23T12:00:00+00:00")
CAT = Path(__file__).parent / "fixtures" / "catalogue.json"


@pytest.fixture
def server() -> FakeServer:
    return FakeServer(Catalogue.from_doc(json.loads(CAT.read_text())), role_modes=["reporter", "manager"])


@pytest.fixture
def ctx(tmp_path: Path, server: FakeServer, pack: ContextPack) -> CommandContext:
    state = {"role": "manager"}
    return CommandContext(
        api=server, outbox=Outbox(tmp_path), catalogue_cache=CatalogueCache(tmp_path / "c.json"),
        role_cache=roles.RoleCache(tmp_path / "me.json"), pack=lambda: pack, actor="@mahmoud", workspace="acme",
        now=lambda: NOW, get_role=lambda: state["role"], set_role=lambda m: state.update(role=m),
    )  # fmt: skip


def test_state_lists_only_allowed_modes_chips_and_ops(ctx: CommandContext) -> None:
    st = dispatch("cmd-state", {}, ctx)
    assert [m["mode"] for m in st["modes"]] == ["reporter", "manager"] and st["role"] == "manager"
    assert {"New epic", "Merge"} <= {c["label"] for c in st["chips"]}
    assert any(o["op"] == "item.merge" and o["destructive"] for o in st["ops"])
    assert dispatch("cmd-role", {"mode": "admin"}, ctx)["error"].startswith("role mode")
    rep = dispatch("cmd-role", {"mode": "reporter"}, ctx)
    assert rep["role"] == "reporter" and {o["group"] for o in rep["ops"]} == {"personal"}


def test_every_verb_is_handled(ctx: CommandContext) -> None:
    for v in VERBS:
        out = dispatch(v, {}, ctx)
        assert "error" not in out or "unknown verb" not in out["error"], v
    assert dispatch("nope", {}, ctx)["error"].startswith("unknown verb")


def test_propose_preview_and_the_destructive_gate_is_enforced_in_the_sidecar(
    ctx: CommandContext, server: FakeServer
) -> None:
    prop = dispatch("cmd-propose", {"text": "merge WEB-101 and WEB-105"}, ctx)
    cmds = prop["commands"]
    assert cmds[0]["op"] == "item.merge" and cmds[0]["args"]["_meta"]["role_mode"] == "manager"
    out = dispatch("cmd-preview", {"commands": cmds}, ctx)["outcomes"][0]
    assert (
        out["status"] == "previewed"
        and out["destructive"]
        and {"Removes", "Affects"} <= {r["label"] for r in out["rows"]}
    )
    assert dispatch("cmd-send", {"commands": cmds}, ctx)["status"] == "needs_confirmation"
    assert (
        dispatch("cmd-send", {"commands": cmds, "confirmed": True}, ctx)["status"]
        == "needs_second_confirmation"
    )
    assert not server.state.get("merged")
    sent = dispatch("cmd-send", {"commands": cmds, "confirmed": True, "second_confirmed": True}, ctx)
    assert (
        sent["status"] == "sent"
        and server.state["merged"]
        and server.applied_log[-1]["meta"]["forced"] is False
    )


def test_force_is_marked_and_rejections_are_reported(ctx: CommandContext, server: FakeServer) -> None:
    cmds = dispatch("cmd-propose", {"text": "remove @ali from team payments"}, ctx)["commands"]
    assert dispatch("cmd-send", {"commands": cmds, "force": True}, ctx)["status"] == "sent"
    assert server.applied_log[-1]["meta"]["forced"] is True
    server.rejects["tag.create"] = "no permission"
    tag = dispatch("cmd-propose", {"text": "new tag q4"}, ctx)["commands"]
    res = dispatch("cmd-send", {"commands": tag, "confirmed": True}, ctx)
    assert res["status"] == "rejected" and res["rejected"][0]["errors"][0]["message"] == "no permission"


def test_manual_fields_and_build(ctx: CommandContext) -> None:
    spec = dispatch("cmd-fields", {"op": "team.create"}, ctx)
    names = [f["name"] for f in spec["fields"]]
    assert names[:2] == ["name", "kind"] and spec["fields"][1]["choices"] and spec["fields"][1]["strict"]
    ok = dispatch(
        "cmd-build",
        {"op": "team.create", "answers": {"name": "p", "kind": "virtual", "members": "@sara,@ali"}},
        ctx,
    )
    assert (
        ok["command"]["args"]["members"] == ["@sara", "@ali"]
        and ok["command"]["args"]["_meta"]["mode"] == "manual"
    )
    bad = dispatch("cmd-build", {"op": "team.create", "answers": {"kind": "virtual"}}, ctx)
    assert bad["errors"][0]["pointer"] == "/name"


def test_incomplete_phrase_returns_a_prefilled_form(ctx: CommandContext) -> None:
    prop = dispatch("cmd-propose", {"text": "waive violation V-9"}, ctx)
    assert not prop["commands"] and prop["incomplete"][0]["op"] == "violation.waive"
    assert prop["incomplete"][0]["args"]["id"] == "V-9" and any(
        f["name"] == "reason" and f["required"] for f in prop["incomplete"][0]["form"]["fields"]
    )


def test_queue_team_now_and_resolve(ctx: CommandContext, server: FakeServer) -> None:
    server.review_rows = [{"id": "RV-3", "summary": "x", "default_choice": "accept"}]
    server.team_activities = [{"person": "@sara"}]
    assert dispatch("cmd-queue", {}, ctx)["rows"][0]["id"] == "RV-3"
    assert dispatch("cmd-team-now", {}, ctx)["activities"][0]["person"] == "@sara"
    res: Any = dispatch("cmd-resolve", {"rows": [{"id": "RV-3", "choice": "accept"}]}, ctx)
    assert res["status"] == "sent" and server.state["commands"][-1]["args"] == {
        "row": "RV-3",
        "choice": {"choice": "accept"},
    }


def test_offline_send_is_queued(ctx: CommandContext, server: FakeServer) -> None:
    cmds = dispatch("cmd-propose", {"text": "new tag q4"}, ctx)["commands"]
    server.offline = True
    assert dispatch("cmd-send", {"commands": cmds, "confirmed": True}, ctx)["status"] == "queued"
    server.offline = False
    from tt_client.outbox import sync

    sync(server, ctx.outbox)
    assert server.state["tags"] == ["q4"]
