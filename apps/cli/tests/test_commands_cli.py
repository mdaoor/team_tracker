"""CLI e2e for Phase 14 against the fake command server: role, ops, do (assisted), cmd (manual), safety, mixed batch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from tt_cli.main import app
from tt_client.api import TTApi
from tt_client.catalogue import Catalogue
from tt_client.config import ClientConfig
from tt_client.testing.fake_command_server import FakeServer
from typer.testing import CliRunner

runner = CliRunner()
HERE = Path(__file__).parent
CATALOGUE = HERE.parents[2] / "packages" / "client_core" / "tests" / "fixtures" / "catalogue.json"
NOW = "2026-10-23T12:00:00+00:00"
PACK: dict[str, Any] = {
    "context_schema_version": 1, "actor": "@mahmoud", "workspace": "acme",
    "projects": [{"key": "WEB", "name": "Website", "aliases": ["site"]}, {"key": "DATA", "name": "Ingestion", "aliases": []}],
    "my_open_items": [{"key": "WEB-101", "title": "Login", "project": "WEB"}],
    "people": [{"handle": "@sara", "name": "Sara"}, {"handle": "@ali", "name": "Ali"}],
    "tags": [], "milestones": [], "iterations": [{"name": "Sprint 14", "project": "WEB"}], "risks": [], "stages": {},
    "manager_rules": [], "glossary": {}, "manual_mode": {"required_fields": ["action", "item", "project"]},
    "my_role_modes": ["reporter", "manager"],
    "my_teams_with_members": [{"name": "payments", "members": ["@sara"]}],
    "pending_approvals": [{"id": "AP-12", "title": "scope change"}],
}  # fmt: skip


@pytest.fixture
def server() -> FakeServer:
    return FakeServer(
        Catalogue.from_doc(json.loads(CATALOGUE.read_text())), pack=PACK, role_modes=["reporter", "manager"]
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: FakeServer) -> Path:
    monkeypatch.setenv("TT_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TT_NOW", NOW)
    monkeypatch.delenv("TT_TOKEN", raising=False)
    ClientConfig(
        server_url="http://tt", workspace="acme", handle="@mahmoud", token="t", time_zone="UTC"
    ).save(tmp_path / "cfg" / "config.toml")

    def make_api(cfg: ClientConfig, **kw: Any) -> TTApi:
        client = httpx.Client(transport=httpx.MockTransport(server.handle), base_url="http://tt")
        return TTApi(cfg, client=client)

    monkeypatch.setattr("tt_cli.runtime.TTApi", make_api)
    return tmp_path


def tt(*args: str, input: str | None = None, code: int = 0) -> str:
    res = runner.invoke(app, list(args), input=input)
    assert res.exit_code == code, f"tt {' '.join(args)} -> {res.exit_code}\n{res.output}"
    return res.output


def test_role_lists_only_allowed_modes_and_persists(home: Path) -> None:
    out = tt("role")
    assert "reporter" in out and "manager" in out and "scrum_master" not in out and "* reporter" in out
    assert "not available" in tt("role", "admin", code=2)
    assert "role: Manager" in tt("role", "manager")
    assert 'role_mode = "manager"' in (home / "cfg" / "config.toml").read_text()
    assert "* manager" in tt("role")
    assert "role: Reporter" in tt("role", "reporter")


def test_ops_follow_the_role_and_all_reveals_the_rest(home: Path) -> None:
    rep = tt("ops")
    assert (
        "dailylog.confirm" in rep and "item.merge" not in rep and "team.create" not in rep
    )  # no management verbs
    assert "item.merge" in tt("ops", "--all")
    tt("role", "manager")
    mgr = tt("ops")
    assert "item.merge" in mgr and "team.create" in mgr
    assert [
        o["op"] for o in json.loads(tt("ops", "--json", "--role", "coach")) if o["op"].startswith("item.")
    ] == []


def test_do_merge_preview_confirm_sent(
    home: Path, server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    tt("role", "manager")
    monkeypatch.setattr("tt_cli.commands_cli.is_interactive", lambda: True)
    out = tt("do", "merge WEB-101 and WEB-105", input="n\n")  # first question: Send? -> no
    assert "Effect preview" in out and "Send?" in out and not server.state.get("merged")
    out = tt("do", "merge WEB-101 and WEB-105", input="y\nn\n")  # destructive: a second, explicit question
    assert "Really send?" in out and not server.state.get("merged")
    out = tt("do", "merge WEB-101 and WEB-105", input="y\ny\n")
    assert "Removes" in out and "WEB-105" in out and "Overrides kept" in out and "✓ item.merge" in out
    assert server.state["merged"][0]["sources"] == ["WEB-101", "WEB-105"]
    assert [c["op"] for c in server.previews] == ["item.merge"] * 3  # previews never wrote anything
    last = [e for e in server.applied_log if e["kind"] == "command"][-1]
    assert (
        last["meta"]["forced"] is False
        and last["meta"]["role_mode"] == "manager"
        and last["meta"]["mode"] == "assisted"
    )
    assert last["meta"]["client_version"] == "0.2"


def test_non_interactive_needs_flags_and_yes_alone_cannot_merge(home: Path, server: FakeServer) -> None:
    tt("role", "manager")
    out = tt("do", "merge WEB-101 and WEB-105", "--yes", code=3)
    assert not server.state.get("merged") and "--force" in out
    out = tt("do", "merge WEB-101 and WEB-105", "--yes", "--force")
    assert "✓ item.merge" in out and server.state["merged"]
    sent = [e for e in server.applied_log if e["kind"] == "command"]
    assert sent[-1]["meta"]["forced"] is True  # --force is logged


def test_destructive_with_yes_only_never_sends(home: Path, server: FakeServer) -> None:
    tt("role", "manager")
    tt("do", "remove @sara from team payments", "--yes", code=3)
    assert not [e for e in server.applied_log if e["kind"] == "command"]
    pending = list((home / "data" / "outbox").glob("*.json"))
    assert len(pending) == 1  # kept in the outbox, ready for `tt send --yes --force`
    tt("send", json.loads(pending[0].read_text())["commands"][0]["client_id"][:8], "--yes", "--force")
    assert [e["op"] for e in server.applied_log if e["kind"] == "command"] == ["team.remove_members"]


def test_ordinary_command_sends_with_yes_and_records_no_force(home: Path, server: FakeServer) -> None:
    tt("role", "manager")
    out = tt("do", "tag WEB-101 #q4", "--yes")
    assert "✓ item.tag" in out
    assert server.applied_log[-1]["meta"]["forced"] is False


def test_rejected_command_exits_nonzero_and_nothing_is_written(home: Path, server: FakeServer) -> None:
    server.rejects["project.create"] = "role manager may not create projects"
    tt("role", "manager")
    out = tt("do", "new project Payments key PAY scrum", "--yes", code=1)
    assert "rejected" in out and "may not create projects" in out
    assert not server.state["commands"]


def test_cmd_manual_with_args_and_offline_validation(home: Path, server: FakeServer) -> None:
    tt("role", "manager")
    out = tt(
        "cmd",
        "team.create",
        "--arg",
        "name=payments",
        "--arg",
        "kind=virtual",
        "--arg",
        "members=sara,@ali",
        "--arg",
        "lead=me",
        "--yes",
    )
    assert "✓ team.create" in out
    args = server.state["commands"][-1]["args"]
    assert args["members"] == ["@sara", "@ali"] and args["lead"] == "@mahmoud"
    assert "needs: name" in tt("cmd", "team.create", code=2)
    assert "unknown op" in tt("cmd", "team.explode", code=2)
    assert "invalid arguments" in tt(
        "cmd", "item.set_impact", "--arg", "keys=WEB-1", "--arg", "impact=heavy", code=2
    )


def test_cmd_offline_queues_then_sync_delivers(home: Path, server: FakeServer) -> None:
    tt("role", "manager")
    tt("ops")  # warms the catalogue cache
    server.offline = True
    out = tt("cmd", "tag.create", "--arg", "name=q4", "--yes")  # preview unavailable, staged, queued
    assert "offline" in out and not server.state["tags"]
    server.offline = False
    tt("sync")
    assert server.state["tags"] == ["q4"]


def test_mixed_batch_capture_and_command_in_one_file(home: Path, server: FakeServer) -> None:
    tt("role", "manager")
    tt("do", "new tag q4", "--and", "fix the login redirect loop, took 2h", "--yes")
    kinds = [e["kind"] for e in server.applied_log]
    assert kinds == ["message", "command"] or kinds[-2:] == ["message", "command"]
    assert len({e["correlation_id"] for e in server.applied_log}) == 1
    sent = list((home / "data" / "outbox" / "sent").glob("*.json"))
    assert len(sent) == 1 and json.loads(sent[0].read_text())["commands"]


def test_capture_still_works_in_manager_mode(home: Path, server: FakeServer) -> None:
    tt("role", "manager")
    out = tt("fix the login redirect loop, took 2h", "-p", "web", "--yes")
    assert "✓" in out
    assert [e["kind"] for e in server.applied_log] == ["message"]


def test_approve_waive_queue_and_team_now(home: Path, server: FakeServer) -> None:
    tt("role", "manager")
    assert "AP-12" in tt("approve")
    assert "✓ approval.decide" in tt("approve", "AP-12", "-c", "ok", "--yes")
    assert server.state["commands"][-1]["args"] == {
        "approval": "AP-12",
        "decision": "approved",
        "comment": "ok",
    }
    assert "reason is required" in tt("waive", "V-9", code=2)
    assert "✓ violation.waive" in tt("waive", "V-9", "--reason", "holiday week", "--yes")
    server.review_rows = [
        {"id": "RV-3", "summary": "Tag auth?", "default_choice": "accept", "choices": ["accept", "reject"]},
        {"id": "RV-4", "summary": "Merge dupes?", "default_choice": "accept"},
    ]
    out = tt("queue", "--accept-defaults")
    assert out.count("✓ review.resolve") == 2
    assert [c["args"]["row"] for c in server.state["commands"] if c["op"] == "review.resolve"] == [
        "RV-3",
        "RV-4",
    ]
    server.team_activities = [{"person": {"handle": "@sara"}, "item": {"key": "WEB-1", "title": "Login"}}]
    assert "@sara" in tt("team", "now")


def test_preview_command_for_saved_entry(home: Path, server: FakeServer) -> None:
    tt("role", "manager")
    out = tt("do", "merge WEB-101 and WEB-105", "--no-send")
    assert "saved" in out
    entry = json.loads(next((home / "data" / "outbox").glob("*.json")).read_text())["commands"][0][
        "client_id"
    ][:8]
    assert "Effect preview" in tt("preview", entry) and not server.state.get("merged")


def test_unknown_phrase_without_gateway_points_to_forms(home: Path) -> None:
    tt("role", "manager")
    out = tt("do", "make everyone happy", code=2)
    assert "tt cmd" in out


def test_gateway_fallback_is_validated_against_the_catalogue(
    home: Path, server: FakeServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeTranslator:
        def translate(self, line: str, pack: Any, **kw: Any) -> tuple[list[dict[str, Any]], list[str]]:
            return [
                {"op": "tag.create", "args": {"name": "q4"}},
                {"op": "db.drop_everything", "args": {}},  # not in the catalogue: must be dropped
                {
                    "op": "item.set_impact",
                    "args": {"keys": ["WEB-1"], "impact": "heavy"},
                },  # schema-invalid: dropped
            ], ["model note"]

    monkeypatch.setattr("tt_cli.commands_cli._translator", lambda rt: FakeTranslator())
    tt("role", "manager")
    out = tt("do", "make a q4 tag please", "--yes")
    assert (
        "✓ tag.create" in out and "dropped 'db.drop_everything'" in out and "dropped item.set_impact" in out
    )
    assert [c["op"] for c in server.state["commands"]] == ["tag.create"]


def test_json_dry_run_prints_a_valid_batch(home: Path) -> None:
    tt("role", "manager")
    out = tt("do", "tag WEB-101 #q4", "--json")
    doc = json.loads(out[out.index("{") :])
    assert doc["client_version"] == "0.2" and doc["messages"] == [] and doc["commands"][0]["op"] == "item.tag"
