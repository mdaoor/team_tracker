"""Phase 14 Slack: `/tt do`, command card + Effect preview, destructive second confirmation, approval / waive buttons, generated modals."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tt_client.catalogue import Catalogue, CatalogueCache
from tt_client.outbox import Outbox
from tt_client.pack import ContextPack
from tt_client.parser.llm import NullTranslator
from tt_client.parser.structured import ParseContext
from tt_client.roles import RoleCache
from tt_client.testing.fake_command_server import FakeServer
from tt_slack import command_blocks as cb
from tt_slack.bot import SlackBot
from tt_slack.linking import LinkStore

NOW = datetime.fromisoformat("2026-10-23T12:00:00+00:00")
CATALOGUE = Path(__file__).parents[3] / "packages" / "client_core" / "tests" / "fixtures" / "catalogue.json"
PACK = {
    "context_schema_version": 1, "actor": "@mahmoud", "workspace": "acme",
    "projects": [{"key": "WEB", "name": "Website", "aliases": ["site"]}, {"key": "DATA", "name": "Ingestion", "aliases": []}],
    "my_open_items": [{"key": "WEB-101", "title": "Login", "project": "WEB"}],
    "people": [{"handle": "@sara", "name": "Sara"}, {"handle": "@ali", "name": "Ali"}],
    "tags": [], "milestones": [], "iterations": [], "risks": [], "stages": {}, "manager_rules": [], "glossary": {},
    "manual_mode": {"required_fields": ["action", "item", "project"]},
    "my_teams_with_members": [{"name": "payments", "members": ["@sara", "@ali"]}],
    "pending_approvals": [{"id": "AP-12", "title": "scope change"}, {"id": "AP-13", "title": "extra budget"}],
}  # fmt: skip


class Rt:
    def __init__(self, data: Path, server: FakeServer) -> None:
        self.outbox, self.api, self.workspace = Outbox(data), server, "acme"
        self.cfg = SimpleNamespace(handle="@mahmoud", time_zone="UTC", hours_per_day=8, role_mode="manager")
        self.catalogue_cache, self.role_cache = CatalogueCache(data / "cat.json"), RoleCache(data / "me.json")

    def pack(self, *, refresh: bool = True) -> ContextPack:
        return ContextPack(json.loads(json.dumps(PACK)))

    def translator(self) -> NullTranslator:
        return NullTranslator()

    def parse_ctx(self) -> ParseContext:
        return ParseContext(actor="@mahmoud", now=NOW, tz="UTC")


@pytest.fixture
def server() -> FakeServer:
    return FakeServer(
        Catalogue.from_doc(json.loads(CATALOGUE.read_text())),
        role_modes=["reporter", "manager", "scrum_master"],
    )


@pytest.fixture
def bot(tmp_path: Path, server: FakeServer) -> SlackBot:
    links = LinkStore(tmp_path / "links.json", "s3cret")
    state = links.begin("U1", now=1000)
    links.complete(state, "@mahmoud", "tok", "acme", now=1001)
    rts: dict[str, Rt] = {}
    return SlackBot(
        links,
        lambda link, user: rts.setdefault(user, Rt(tmp_path / f"u-{user}", server)),
        "https://tt.example",
    )


def buttons(blocks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {e["action_id"]: e for b in blocks if b["type"] == "actions" for e in b["elements"]}


def test_do_makes_a_command_card_with_an_effect_preview(bot: SlackBot, server: FakeServer) -> None:
    r = bot.handle_slash("U1", "do tag WEB-101 #q4")
    text = json.dumps(r.blocks)
    assert "item.tag" in text and "Effect preview" in text and "Affects" in text
    assert set(buttons(r.blocks)) == {cb.ACTION_CONFIRM, cb.ACTION_EDIT, cb.ACTION_DISCARD}
    assert "confirm" not in buttons(r.blocks)[cb.ACTION_CONFIRM]  # ordinary command: one click
    assert not [e for e in server.applied_log if e["kind"] == "command"]  # previews never write
    assert "role: Manager" in text


def test_confirm_applies_once(bot: SlackBot, server: FakeServer) -> None:
    card = bot.handle_slash("U1", "do new tag q4")
    confirm = buttons(card.blocks)[cb.ACTION_CONFIRM]
    done = bot.handle_action("U1", cb.ACTION_CONFIRM, confirm["value"])
    assert done.replace and ":white_check_mark: `tag.create`" in done.text and server.state["tags"] == ["q4"]
    again = bot.handle_action("U1", cb.ACTION_CONFIRM, confirm["value"])
    assert again.ephemeral and server.state["tags"] == ["q4"]
    assert (
        server.applied_log[-1]["meta"]["mode"] == "assisted"
        and server.applied_log[-1]["meta"]["role_mode"] == "manager"
    )


def test_destructive_needs_the_second_confirmation(bot: SlackBot, server: FakeServer) -> None:
    card = bot.handle_slash("U1", "do merge WEB-101 and WEB-105")
    confirm = buttons(card.blocks)[cb.ACTION_CONFIRM]
    assert (
        confirm["style"] == "danger" and confirm["confirm"]["confirm"]["text"] == "Yes, do it"
    )  # Slack's native dialog
    assert "destructive" in json.dumps(card.blocks)
    info = json.loads(confirm["value"])
    assert info["second"] is True
    forged = bot.handle_action("U1", cb.ACTION_CONFIRM, json.dumps({"batch": info["batch"], "second": False}))
    assert forged.ephemeral and "destructive" in forged.text and not server.state.get("merged")
    bare = bot.handle_action(
        "U1", cb.ACTION_CONFIRM, info["batch"]
    )  # a raw batch id is not a confirmation either
    assert bare.ephemeral and not server.state.get("merged")
    ok = bot.handle_action("U1", cb.ACTION_CONFIRM, confirm["value"])
    assert (
        "item.merge" in ok.text
        and server.state["merged"]
        and server.applied_log[-1]["meta"]["forced"] is False
    )


def test_rejected_preview_has_no_confirm_button(bot: SlackBot, server: FakeServer) -> None:
    server.rejects["team.create"] = "you are not a manager of this team"
    r = bot.handle_slash("U1", "do create virtual team payments with @sara @ali led by me")
    assert (
        cb.ACTION_CONFIRM not in buttons(r.blocks)
        and "rejected" in json.dumps(r.blocks)
        and "not a manager" in json.dumps(r.blocks)
    )


def test_discard_and_unknown_phrase(bot: SlackBot) -> None:
    card = bot.handle_slash("U1", "do tag WEB-101 #q4")
    bid = buttons(card.blocks)[cb.ACTION_DISCARD]["value"]
    assert bot.handle_action("U1", cb.ACTION_DISCARD, bid).text == "Discarded."
    assert bot.handle_action("U1", cb.ACTION_CONFIRM, bid).ephemeral
    r = bot.handle_slash("U1", "do make everybody happy")
    assert r.ephemeral and "/tt cmd" in r.text


def test_approvals_are_message_buttons_that_decide_in_one_click(bot: SlackBot, server: FakeServer) -> None:
    listing = bot.handle_slash("U1", "approvals")
    b = buttons(listing.blocks)
    assert set(b) == {cb.ACTION_APPROVE, cb.ACTION_REJECT} and "AP-12" in json.dumps(listing.blocks)
    r = bot.handle_action("U1", cb.ACTION_APPROVE, "AP-12")
    assert r.replace and ":white_check_mark: `approval.decide`" in r.text
    assert server.state["commands"][-1]["args"] == {"approval": "AP-12", "decision": "approve"}
    bot.handle_action("U1", cb.ACTION_REJECT, "AP-13")
    assert server.state["commands"][-1]["args"]["decision"] == "reject"
    blocks = cb.approval_message({"id": "AP-1", "title": "x"})
    assert (
        buttons(blocks)[cb.ACTION_APPROVE]["style"] == "primary"
        and buttons(blocks)[cb.ACTION_REJECT]["style"] == "danger"
    )


def test_a_rejected_approval_says_why(bot: SlackBot, server: FakeServer) -> None:
    server.rejects["approval.decide"] = "only the approver may decide"
    r = bot.handle_action("U1", cb.ACTION_APPROVE, "AP-12")
    assert (
        ":x: `approval.decide` rejected: only the approver may decide" in r.text
        and not server.state["commands"]
    )


def test_waive_button_opens_a_reason_modal_and_then_a_card(bot: SlackBot, server: FakeServer) -> None:
    msg = cb.waive_message({"id": "V-9", "title": "late log"})
    assert buttons(msg)[cb.ACTION_WAIVE]["value"] == "V-9"
    opened = bot.handle_action("U1", cb.ACTION_WAIVE, "V-9")
    assert opened.modal and opened.modal["blocks"][0]["block_id"] == "reason"
    state = {"values": {"reason": {"reason": {"type": "plain_text_input", "value": "holiday week"}}}}
    card = bot.handle_command_modal_submit("U1", state, opened.modal["private_metadata"])
    assert "violation.waive" in json.dumps(card.blocks) and "holiday week" in json.dumps(card.blocks)
    bot.handle_action("U1", cb.ACTION_CONFIRM, buttons(card.blocks)[cb.ACTION_CONFIRM]["value"])
    assert server.state["commands"][-1]["args"] == {"id": "V-9", "reason": "holiday week"}
    empty = bot.handle_command_modal_submit(
        "U1",
        {"values": {"reason": {"reason": {"type": "plain_text_input", "value": ""}}}},
        opened.modal["private_metadata"],
    )
    assert empty.response and "reason" in empty.response["errors"]
    inline = bot.handle_slash("U1", "waive V-9 because holiday week")
    assert "violation.waive" in json.dumps(inline.blocks)
    assert bot.handle_slash("U1", "waive V-9").modal is not None


def test_cmd_opens_a_modal_generated_from_the_catalogue_for_every_op(
    bot: SlackBot, server: FakeServer
) -> None:
    cat = server.catalogue_obj
    for meta in cat:
        r = bot.handle_slash("U1", f"cmd {meta.op}")
        m = r.modal
        assert m and m["callback_id"] == cb.MODAL_CALLBACK, meta.op
        by = {b["block_id"]: b for b in m["blocks"]}
        assert set(by) == set(meta.properties), meta.op
        for name in meta.required:
            assert by[name]["optional"] is False, (meta.op, name)
        assert len(m["blocks"]) <= 100 and len(m["title"]["text"]) <= 24
    team = {b["block_id"]: b for b in bot.handle_slash("U1", "cmd team.create").modal["blocks"]}  # type: ignore[index]
    assert team["kind"]["element"]["type"] == "static_select"
    assert team["members"]["element"]["type"] == "multi_static_select"
    assert [o["value"] for o in team["members"]["element"]["options"]] == [
        "@sara",
        "@ali",
    ]  # pack-backed picker
    assert "Unknown op" in bot.handle_slash("U1", "cmd nope").text


def test_modal_submit_builds_a_command_and_reports_field_errors(bot: SlackBot, server: FakeServer) -> None:
    modal = bot.handle_slash("U1", "cmd team.create").modal
    assert modal
    state = {"values": {
        "name": {"name": {"type": "plain_text_input", "value": "payments"}},
        "kind": {"kind": {"type": "static_select", "selected_option": {"value": "virtual"}}},
        "lead": {"lead": {"type": "static_select", "selected_option": {"value": "@sara"}}},
        "members": {"members": {"type": "multi_static_select", "selected_options": [{"value": "@sara"}, {"value": "@ali"}]}},
    }}  # fmt: skip
    card = bot.handle_command_modal_submit("U1", state, modal["private_metadata"])
    assert "team.create" in json.dumps(card.blocks) and "Creates" in json.dumps(card.blocks)
    bot.handle_action("U1", cb.ACTION_CONFIRM, buttons(card.blocks)[cb.ACTION_CONFIRM]["value"])
    assert server.state["commands"][-1]["args"]["members"] == ["@sara", "@ali"]
    bad = bot.handle_command_modal_submit(
        "U1",
        {"values": {"name": {"name": {"type": "plain_text_input", "value": ""}}}},
        modal["private_metadata"],
    )
    assert bad.response and bad.response["response_action"] == "errors" and bad.response["errors"]


def test_edit_reopens_the_form_prefilled_and_replaces_the_card(bot: SlackBot, server: FakeServer) -> None:
    card = bot.handle_slash("U1", "do assign WEB-101 to @sara")
    edit = buttons(card.blocks)[cb.ACTION_EDIT]
    modal = bot.handle_action("U1", cb.ACTION_EDIT, edit["value"]).modal
    assert modal
    by = {b["block_id"]: b for b in modal["blocks"]}
    assert by["person"]["element"]["initial_option"]["value"] == "@sara"
    state = {"values": {
        "key": {"key": {"type": "plain_text_input", "value": "WEB-101"}},
        "person": {"person": {"type": "static_select", "selected_option": {"value": "@ali"}}},
        "role": {"role": {"type": "static_select", "selected_option": {"value": "reviewer"}}},
    }}  # fmt: skip
    new = bot.handle_command_modal_submit("U1", state, modal["private_metadata"])
    assert new.replace and "@ali" in json.dumps(new.blocks)
    assert (
        len(list(bot.runtime_for(None, "U1").outbox.dir.glob("*.json"))) == 1
    )  # the old card's file was replaced


def test_role_switch_offers_only_allowed_modes_and_changes_ops(bot: SlackBot) -> None:
    listing = bot.handle_slash("U1", "role")
    assert "manager" in listing.text and "scrum_master" in listing.text and "admin" not in listing.text
    assert "not available" in bot.handle_slash("U1", "role admin").text
    assert "Scrum master" in bot.handle_slash("U1", "role scrum").text
    ops = bot.handle_slash("U1", "ops").text
    assert "iteration.create" in ops and "team.create" not in ops
    bot.handle_slash("U1", "role reporter")
    rep = bot.handle_slash("U1", "ops").text
    assert "dailylog.confirm" in rep and "item.merge" not in rep


def test_capture_dm_is_unchanged_in_every_role(bot: SlackBot) -> None:
    r = bot.handle_dm("U1", "fixed login loop WEB-101, took 2h", now=NOW)
    assert [e["action_id"] for b in r.blocks if b["type"] == "actions" for e in b["elements"]] == [
        "tt_send",
        "tt_edit",
        "tt_discard",
        "tt_manual",
    ]
