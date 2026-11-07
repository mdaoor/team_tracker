"""Slack shell (plan phase 11 §4): card, Manual modal built from the pack, mandatory fields, linking, Jira chip, exactly-once send."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tt_client.api import Unreachable
from tt_client.outbox import Outbox
from tt_client.pack import ContextPack
from tt_client.parser.llm import NullTranslator
from tt_client.parser.structured import ParseContext
from tt_slack import blocks as bk
from tt_slack.bot import SlackBot
from tt_slack.linking import LinkError, LinkStore

NOW = datetime.fromisoformat("2026-10-23T12:00:00+00:00")
PACK = {
    "context_schema_version": 1, "actor": "@mahmoud", "workspace": "acme",
    "projects": [
        {"key": "WEB", "name": "Website", "aliases": ["site"], "jira": "WEBJ", "jira_auto_send": False},
        {"key": "DATA", "name": "Ingestion", "aliases": [], "jira": None},
    ],
    "my_open_items": [{"key": "WEB-142", "title": "fix the login redirect loop", "project": "WEB"}],
    "people": [{"handle": "@sara", "name": "Sara"}, {"handle": "@ali", "name": "Ali"}],
    "tags": ["auth", "bug"], "milestones": [], "iterations": [], "risks": [], "stages": {}, "priorities": ["low", "high"],
    "manager_rules": [], "glossary": {},
    "manual_mode": {"required_fields": ["action", "item", "project"], "per_project": {"DATA": ["action", "project", "minutes"]}},
}  # fmt: skip


class FakeApi:
    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []
        self.offline = False
        self.logs: list[dict[str, Any]] = [{"id": "L1", "summary": "Fixed login", "status": "draft"}]
        self.confirmed: list[str] = []

    def post_batch(self, batch: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.offline:
            raise Unreachable("down")
        self.posted.append(batch)
        return 200, {
            "results": [
                {
                    "status": "applied",
                    "client_id": m["client_id"],
                    "item": {"key": "WEB-1", "title": m.get("note") or "t"},
                }
                for m in batch["messages"]
            ]
        }

    def active_activities(self) -> list[dict[str, Any]]:
        return [{"key": "WEB-142", "title": "login loop"}]

    def daily_logs(self, day: str | None = None) -> list[dict[str, Any]]:
        return self.logs

    def confirm_daily_log(self, log_id: str) -> dict[str, Any]:
        self.confirmed.append(log_id)
        return {}


class FakeRuntime:
    def __init__(self, data: Path, api: FakeApi) -> None:
        self.outbox, self.api, self.workspace = Outbox(data), api, "acme"
        self.cfg = SimpleNamespace(handle="@mahmoud", time_zone="UTC", hours_per_day=8)

    def pack(self, *, refresh: bool = True) -> ContextPack:
        return ContextPack(json.loads(json.dumps(PACK)))

    def translator(self) -> NullTranslator:
        return NullTranslator()

    def parse_ctx(self) -> ParseContext:
        return ParseContext(actor="@mahmoud", now=NOW, tz="UTC")


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def bot(tmp_path: Path, api: FakeApi) -> SlackBot:
    links = LinkStore(tmp_path / "links.json", "s3cret")
    state = links.begin("U1", now=1000)
    links.complete(state, "@mahmoud", "tok", "acme", now=1001)
    rts: dict[str, FakeRuntime] = {}

    def runtime_for(link: Any, user: str) -> FakeRuntime:
        return rts.setdefault(user, FakeRuntime(tmp_path / f"u-{user}", api))

    return SlackBot(links, runtime_for, "https://tt.example")


def action_ids(blocks: list[dict[str, Any]]) -> list[str]:
    return [e["action_id"] for b in blocks if b["type"] == "actions" for e in b["elements"]]


# ---------------------------------------------------------------- card
def test_dm_becomes_a_card_with_four_buttons_and_the_json_in_the_thread(bot: SlackBot) -> None:
    r = bot.handle_dm("U1", "fixed login loop WEB-142, took 2h #auth", now=NOW)
    assert action_ids(r.blocks) == ["tt_send", "tt_edit", "tt_discard", "tt_manual"]
    text = json.dumps(r.blocks)
    assert "WEB-142" in text and "2 h logged" in text and "#auth" in text
    batch = json.loads(r.snippet or "")
    assert batch["workspace"] == "acme" and batch["messages"][0]["minutes"] == 120
    assert any(b["type"] == "context" for b in r.blocks)  # "parsed without the model"


def test_card_snapshot(bot: SlackBot) -> None:
    r = bot.handle_dm("U1", "doing WEB-142 #auth", now=NOW)
    first = r.blocks[0]
    assert first == {"type": "header", "text": {"type": "plain_text", "text": "Start"}}
    sections = [b["text"]["text"] for b in r.blocks if b["type"] == "section"]
    assert sections[0].startswith("*Task*  fix the login redirect loop WEB-142")
    btn = next(
        e for b in r.blocks if b["type"] == "actions" for e in b["elements"] if e["action_id"] == "tt_send"
    )
    assert btn["style"] == "primary" and btn["text"]["text"] == "Send" and btn["value"]


def test_empty_and_unlinked_users_get_ephemeral_guidance(bot: SlackBot) -> None:
    assert bot.handle_dm("U1", "   ").ephemeral
    r = bot.handle_dm("U2", "hello")
    assert r.ephemeral and "https://tt.example/slack/link?state=U2." in r.text


# ---------------------------------------------------------------- send / discard / offline
def test_send_posts_once_and_a_second_click_is_harmless(bot: SlackBot, api: FakeApi) -> None:
    card = bot.handle_dm("U1", "fixed it, took 1h", now=NOW)
    bid = next(
        e["value"]
        for b in card.blocks
        if b["type"] == "actions"
        for e in b["elements"]
        if e["action_id"] == "tt_send"
    )
    first = bot.handle_action("U1", bk.ACTION_SEND, bid)
    assert first.replace and ":white_check_mark:" in first.text and len(api.posted) == 1
    again = bot.handle_action("U1", bk.ACTION_SEND, bid)
    assert again.ephemeral and len(api.posted) == 1  # the file was moved to sent/: no double send


def test_offline_send_queues_and_discard_removes(bot: SlackBot, api: FakeApi) -> None:
    api.offline = True
    card = bot.handle_dm("U1", "note: waiting on ops", now=NOW)
    bid = card.blocks[-2]["elements"][0]["value"]
    r = bot.handle_action("U1", bk.ACTION_SEND, bid)
    assert "Offline" in r.text and not api.posted
    api.offline = False
    assert bot.handle_action("U1", bk.ACTION_SEND, bid).text.startswith(":white_check_mark:")
    other = bot.handle_dm("U1", "note: another", now=NOW)
    oid = other.blocks[-2]["elements"][0]["value"]
    assert bot.handle_action("U1", bk.ACTION_DISCARD, oid).text == "Discarded."
    assert bot.handle_action("U1", bk.ACTION_SEND, oid).ephemeral


# ---------------------------------------------------------------- manual modal
def test_manual_modal_lists_exactly_the_packs_choices(bot: SlackBot) -> None:
    card = bot.handle_dm("U1", "fixed it", now=NOW)
    m = bot.handle_action("U1", bk.ACTION_MANUAL, card.blocks[-2]["elements"][-1]["value"]).modal
    assert m and m["callback_id"] == bk.MODAL_CALLBACK
    by = {b["block_id"]: b for b in m["blocks"]}

    def opts(name: str) -> list[str]:
        return [o["value"] for o in by[name]["element"]["options"]]

    assert opts("project") == ["WEB", "DATA"] and opts("tags") == ["auth", "bug"]
    assert opts("item") == ["WEB-142  fix the login redirect loop"][:1]
    assert not by["action"]["optional"] and not by["project"]["optional"] and not by["item"]["optional"]
    assert by["minutes"]["optional"]


def test_edit_prefills_the_modal_from_the_file(bot: SlackBot) -> None:
    card = bot.handle_dm("U1", "fixed WEB-142, took 90m #bug p:web", now=NOW)
    bid = card.blocks[-2]["elements"][0]["value"]
    m = bot.handle_action("U1", bk.ACTION_EDIT, bid).modal
    by = {b["block_id"]: b for b in m["blocks"]}  # type: ignore[index]
    assert by["minutes"]["element"]["initial_value"] == "90"
    assert by["tags"]["element"]["initial_options"][0]["value"] == "bug"


def view(**values: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in values.items():
        if isinstance(v, list):
            out[k] = {k: {"type": "multi_static_select", "selected_options": [{"value": x} for x in v]}}
        elif k in ("minutes", "note", "planned_end"):
            out[k] = {k: {"type": "plain_text_input", "value": v}}
        else:
            out[k] = {k: {"type": "static_select", "selected_option": {"value": v} if v else None}}
    return {"values": out}


def test_modal_enforces_mandatory_fields_per_project_and_reports_them(bot: SlackBot) -> None:
    r = bot.handle_modal_submit("U1", view(action="note", project="WEB"), "", now=NOW)
    assert r.response == bk.modal_errors(["item"])
    r = bot.handle_modal_submit("U1", view(action="log_time", project="DATA", minutes=""), "", now=NOW)
    assert r.response and set(r.response["errors"]) == {"minutes"}


def test_modal_submit_builds_a_card_and_replaces_the_old_one(bot: SlackBot) -> None:
    card = bot.handle_dm("U1", "fixed it", now=NOW)
    bid = card.blocks[-2]["elements"][0]["value"]
    r = bot.handle_modal_submit(
        "U1",
        view(
            action="log_time",
            project="WEB",
            item="WEB-142  fix the login redirect loop",
            minutes="45m",
            tags=["auth"],
        ),
        bid,
        now=NOW,
    )
    assert r.response is None and r.replace and "45 min logged" in json.dumps(r.blocks)
    assert json.loads(r.snippet or "")["mode"] == "manual"
    assert bot.handle_action("U1", bk.ACTION_SEND, bid).ephemeral  # the replaced card's file is gone


def test_modal_values_flattening_keeps_only_the_item_key() -> None:
    got = bk.modal_values(view(item="WEB-142  fix it", tags=["a", "b"], note="x"))
    assert got == {"item": "WEB-142", "tags": ["a", "b"], "note": "x"}


# ---------------------------------------------------------------- jira chip
def test_jira_chip_only_for_projects_with_jira_and_toggles_the_flag(bot: SlackBot, api: FakeApi) -> None:
    no = bot.handle_dm("U1", "note: x p:data", now=NOW)
    assert bk.ACTION_JIRA not in action_ids(no.blocks)
    card = bot.handle_dm("U1", "fixed WEB-142 p:web", now=NOW)
    assert bk.ACTION_JIRA in action_ids(card.blocks)
    chip = next(
        e
        for b in card.blocks
        if b["type"] == "actions"
        for e in b["elements"]
        if e["action_id"] == bk.ACTION_JIRA
    )
    assert chip["text"]["text"] == "Jira: off"
    on = bot.handle_action("U1", bk.ACTION_JIRA, chip["value"])
    assert (
        on.replace
        and next(
            e
            for b in on.blocks
            if b["type"] == "actions"
            for e in b["elements"]
            if e["action_id"] == bk.ACTION_JIRA
        )["text"]["text"]
        == "Jira: on"
    )
    sid = next(
        e["value"]
        for b in on.blocks
        if b["type"] == "actions"
        for e in b["elements"]
        if e["action_id"] == bk.ACTION_SEND
    )
    bot.handle_action("U1", bk.ACTION_SEND, sid)
    assert api.posted[-1]["messages"][0]["jira"] == {"project": "WEBJ", "send": True}


# ---------------------------------------------------------------- slash / end of day
def test_slash_commands_and_end_of_day_never_nag(bot: SlackBot, api: FakeApi) -> None:
    assert "WEB-142" in bot.handle_slash("U1", "now").text
    assert bot.handle_slash("U1", "today").blocks[-1]["elements"][0]["action_id"] == "tt_confirm_log"
    assert bot.end_of_day("U1") is not None
    assert bot.handle_confirm_log("U1", "L1").replace and api.confirmed == ["L1"]
    api.logs[0]["status"] = "confirmed"
    assert bot.end_of_day("U1") is None
    assert bot.end_of_day("U9") is None  # unlinked: no DM
    assert bot.handle_slash("U1", "unlink").text == "Unlinked."
    assert bot.handle_dm("U1", "x").ephemeral


# ---------------------------------------------------------------- linking
def test_link_state_is_signed_expiring_and_the_store_is_private(tmp_path: Path) -> None:
    s = LinkStore(tmp_path / "l.json", "k")
    st = s.begin("U1", now=100)
    with pytest.raises(LinkError):
        s.complete(st[:-1] + ("0" if st[-1] != "0" else "1"), "@a", "t", "w", now=101)
    with pytest.raises(LinkError):
        s.complete(st, "@a", "t", "w", now=100 + 601)
    with pytest.raises(LinkError):
        s.complete("junk", "@a", "t", "w")
    assert s.complete(st, "@a", "t", "w", now=150) == "U1"
    assert s.get("U1").token == "t"  # type: ignore[union-attr]
    assert (tmp_path / "l.json").stat().st_mode & 0o777 == 0o600
    assert (
        LinkStore(tmp_path / "l.json", "other").get("U1") is not None
    )  # reading needs no secret; forging a link does
    with pytest.raises(LinkError):
        LinkStore(tmp_path / "l.json", "other").complete(st, "@evil", "t", "w", now=150)
