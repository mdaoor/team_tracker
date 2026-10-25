"""Phase 13 R3.9: the Bolt transport (`tt_slack/bolt_app.py`) is covered with a stand-in for `slack_bolt` (no Slack, no network)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest
from tt_slack import blocks as bk
from tt_slack import bolt_app
from tt_slack.bot import Reply
from tt_slack.linking import Link


class FakeBoltApp:
    """Records the handlers the transport registers, keyed like Bolt does."""

    instances: list[FakeBoltApp] = []

    def __init__(self, token: str, signing_secret: str) -> None:
        self.token, self.secret = token, signing_secret
        self.handlers: dict[str, Any] = {}
        FakeBoltApp.instances.append(self)

    def _reg(self, kind: str, key: str | None = None):  # type: ignore[no-untyped-def]
        def deco(fn: Any) -> Any:
            self.handlers[f"{kind}:{key}" if key else kind] = fn
            return fn

        return deco

    def event(self, name: str):  # type: ignore[no-untyped-def]
        return self._reg("event", name)

    def action(self, aid: str):  # type: ignore[no-untyped-def]
        return self._reg("action", aid)

    def view(self, cb: str):  # type: ignore[no-untyped-def]
        return self._reg("view", cb)

    def command(self, name: str):  # type: ignore[no-untyped-def]
        return self._reg("command", name)


class FakeBot:
    def __init__(self, links: Any, runtime_for: Any, server: str) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.next_reply = Reply(text="hello", blocks=[{"type": "section"}])

    def handle_dm(self, user: str, text: str) -> Reply:
        self.calls.append(("dm", (user, text)))
        return self.next_reply

    def handle_action(self, user: str, action_id: str, value: str) -> Reply:
        self.calls.append(("action", (user, action_id, value)))
        return self.next_reply

    def handle_modal_submit(self, user: str, state: Any, meta: str) -> Reply:
        self.calls.append(("modal", (user, meta)))
        return self.next_reply

    def handle_slash(self, user: str, text: str) -> Reply:
        self.calls.append(("slash", (user, text)))
        return self.next_reply


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeBoltApp:
    mod = types.ModuleType("slack_bolt")
    mod.App = FakeBoltApp  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "slack_bolt", mod)
    for k, v in {
        "TT_SERVER_URL": "https://tt.test", "TT_SLACK_LINK_SECRET": "s" * 32, "SLACK_BOT_TOKEN": "xoxb-1",
        "SLACK_SIGNING_SECRET": "sig", "TT_SLACK_DATA": str(tmp_path),
    }.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(bolt_app, "SlackBot", FakeBot)
    FakeBoltApp.instances.clear()
    bolt_app.build_app()
    return FakeBoltApp.instances[-1]


class Say:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def __call__(self, **kw: Any) -> None:
        self.sent.append(kw)


def test_every_slack_entry_point_is_registered(app: FakeBoltApp) -> None:
    want = {"event:message", f"view:{bk.MODAL_CALLBACK}", "command:/tt"} | {
        f"action:{a}"
        for a in (bk.ACTION_SEND, bk.ACTION_EDIT, bk.ACTION_DISCARD, bk.ACTION_MANUAL, bk.ACTION_JIRA)
    }
    assert set(app.handlers) == want
    assert app.token == "xoxb-1" and app.secret == "sig"


def test_a_direct_message_is_answered_but_bots_and_channels_are_ignored(app: FakeBoltApp) -> None:
    say = Say()
    app.handlers["event:message"]({"channel_type": "im", "user": "U1", "text": "doing x"}, say)
    assert say.sent == [{"text": "hello", "blocks": [{"type": "section"}]}]
    app.handlers["event:message"]({"channel_type": "im", "user": "U1", "text": "x", "bot_id": "B"}, say)
    app.handlers["event:message"]({"channel_type": "channel", "user": "U1", "text": "x"}, say)
    assert len(say.sent) == 1  # nothing for the bot's own messages or public channels


def test_a_button_acks_first_then_posts_the_reply(app: FakeBoltApp) -> None:
    order: list[str] = []
    say = Say()
    handler = app.handlers[f"action:{bk.ACTION_SEND}"]
    handler(
        lambda: order.append("ack"), {"user": {"id": "U1"}, "trigger_id": "T1"}, Say(), object(), say,
        {"action_id": bk.ACTION_SEND, "value": "batch-1"},
    )  # fmt: skip
    assert order == ["ack"] and say.sent == [{"text": "hello", "blocks": [{"type": "section"}]}]


def test_a_reply_with_a_modal_opens_it(app: FakeBoltApp, monkeypatch: pytest.MonkeyPatch) -> None:
    # rebuild with a bot that answers with a modal
    class ModalBot(FakeBot):
        def handle_action(self, user: str, action_id: str, value: str) -> Reply:
            return Reply(modal={"type": "modal", "callback_id": bk.MODAL_CALLBACK})

    monkeypatch.setattr(bolt_app, "SlackBot", ModalBot)
    FakeBoltApp.instances.clear()
    bolt_app.build_app()
    a = FakeBoltApp.instances[-1]

    class Client:
        opened: list[dict[str, Any]] = []

        def views_open(self, **kw: Any) -> None:
            Client.opened.append(kw)

    a.handlers[f"action:{bk.ACTION_MANUAL}"](
        lambda: None, {"user": {"id": "U1"}, "trigger_id": "T9"}, Say(), Client(), Say(),
        {"action_id": bk.ACTION_MANUAL, "value": "b"},
    )  # fmt: skip
    assert (
        Client.opened[0]["trigger_id"] == "T9"
        and Client.opened[0]["view"]["callback_id"] == bk.MODAL_CALLBACK
    )


def test_replace_and_snippet_paths(app: FakeBoltApp, monkeypatch: pytest.MonkeyPatch) -> None:
    class ReplaceBot(FakeBot):
        def handle_action(self, user: str, action_id: str, value: str) -> Reply:
            return Reply(text="sent", replace=True, snippet='{"a": 1}')

    monkeypatch.setattr(bolt_app, "SlackBot", ReplaceBot)
    FakeBoltApp.instances.clear()
    bolt_app.build_app()
    a = FakeBoltApp.instances[-1]
    say, respond = Say(), Say()
    a.handlers[f"action:{bk.ACTION_SEND}"](
        lambda: None, {"user": {"id": "U1"}, "trigger_id": "T"}, respond, object(), say,
        {"action_id": bk.ACTION_SEND, "value": "b"},
    )  # fmt: skip
    assert respond.sent == [{"text": "sent", "blocks": None, "replace_original": True}]
    assert say.sent == [{"text": '```{"a": 1}```'}]


def test_modal_submit_acks_with_errors_or_posts_the_result(
    app: FakeBoltApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    acked: list[dict[str, Any]] = []
    posted: list[dict[str, Any]] = []

    class Client:
        def chat_postMessage(self, **kw: Any) -> None:
            posted.append(kw)

    body = {"user": {"id": "U1"}}
    view = {"state": {"values": {}}, "private_metadata": "meta"}
    app.handlers[f"view:{bk.MODAL_CALLBACK}"](lambda **kw: acked.append(kw), body, view, Client())
    assert acked == [{}] and posted[0]["channel"] == "U1" and posted[0]["text"] == "hello"

    class ErrBot(FakeBot):
        def handle_modal_submit(self, user: str, state: Any, meta: str) -> Reply:
            return Reply(response={"response_action": "errors", "errors": {"b": "required"}})

    monkeypatch.setattr(bolt_app, "SlackBot", ErrBot)
    FakeBoltApp.instances.clear()
    bolt_app.build_app()
    a = FakeBoltApp.instances[-1]
    acked.clear()
    posted.clear()
    a.handlers[f"view:{bk.MODAL_CALLBACK}"](lambda **kw: acked.append(kw), body, view, Client())
    assert acked == [{"response_action": "errors", "errors": {"b": "required"}}] and posted == []


def test_slash_command_replies_ephemerally(app: FakeBoltApp) -> None:
    respond = Say()
    app.handlers["command:/tt"](lambda: None, {"user_id": "U2", "text": "help"}, respond)
    assert respond.sent == [{"text": "hello", "blocks": [{"type": "section"}], "response_type": "ephemeral"}]


def test_the_runtime_factory_builds_a_per_user_client(tmp_path: Path) -> None:
    rt_cls = bolt_app._runtime_factory("https://tt.test", "http://gw:8700", tmp_path)
    link = Link(workspace="acme", handle="@sara", token="tt_abc")  # type: ignore[call-arg]
    rt = rt_cls(link, "U123")
    assert rt.cfg.handle == "@sara" and rt.cfg.gateway_url == "http://gw:8700"
    assert (tmp_path / "U123").is_dir() and rt.workspace == "acme"
    assert rt.translator().__class__.__name__ == "GatewayTranslator"
    assert rt.parse_ctx().actor == "@sara"
    no_gw = bolt_app._runtime_factory("https://tt.test", None, tmp_path)(link, "U124")
    assert no_gw.translator().__class__.__name__ == "NullTranslator"
    assert no_gw.pack(refresh=False) is None  # nothing cached yet
