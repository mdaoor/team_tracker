"""Bolt transport (optional extra: `pip install tt-slack[bolt]`). All behaviour is in `bot.SlackBot`; this only maps Slack events."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from tt_slack import blocks as bk
from tt_slack import command_blocks as cbk
from tt_slack.bot import Reply, SlackBot
from tt_slack.linking import Link, LinkStore


def _runtime_factory(server_url: str, gateway_url: str | None, data_root: Path):  # type: ignore[no-untyped-def]
    from tt_client.api import TTApi
    from tt_client.catalogue import CatalogueCache
    from tt_client.command_flow import GatewayCommandTranslator
    from tt_client.config import ClientConfig
    from tt_client.outbox import Outbox
    from tt_client.pack import PackCache
    from tt_client.parser.llm import GatewayTranslator, NullTranslator
    from tt_client.parser.structured import ParseContext
    from tt_client.roles import RoleCache

    class _Rt:
        def __init__(self, link: Link, slack_user: str) -> None:
            self.cfg = ClientConfig(
                server_url=server_url,
                workspace=link.workspace,
                handle=link.handle,
                token=link.token,
                gateway_url=gateway_url,
            )
            d = data_root / slack_user
            d.mkdir(parents=True, exist_ok=True)
            self.outbox, self.cache, self.api = Outbox(d), PackCache(d / "pack.json"), TTApi(self.cfg)
            self.workspace = link.workspace
            self.catalogue_cache, self.role_cache = (
                CatalogueCache(d / "catalogue.json"),
                RoleCache(d / "me.json"),
            )

        def pack(self, *, refresh: bool = True):  # type: ignore[no-untyped-def]
            if refresh:
                try:
                    return self.cache.refresh(self.api)
                except Exception:  # noqa: BLE001, S110 - a broken server must never stop capture
                    pass
            return self.cache.load()[0]

        def translator(self):  # type: ignore[no-untyped-def]
            if self.cfg.gateway_url:
                return GatewayTranslator(
                    self.cfg.gateway_url, workspace=self.cfg.workspace, core_token=self.cfg.token
                )
            return NullTranslator()

        def command_translator(self):  # type: ignore[no-untyped-def]
            if not self.cfg.gateway_url:
                return None
            return GatewayCommandTranslator(
                self.cfg.gateway_url, workspace=self.cfg.workspace, core_token=self.cfg.token
            )

        def parse_ctx(self) -> ParseContext:
            from datetime import UTC, datetime

            return ParseContext(actor=self.cfg.handle, now=datetime.now(UTC), tz=self.cfg.time_zone)

    return _Rt


def build_app() -> Any:
    from slack_bolt import App

    server = os.environ["TT_SERVER_URL"]
    data = Path(os.environ.get("TT_SLACK_DATA", "./var/slack"))
    links = LinkStore(data / "links.json", os.environ["TT_SLACK_LINK_SECRET"])
    bot = SlackBot(links, _runtime_factory(server, os.environ.get("TT_GATEWAY_URL"), data), server)
    app = App(token=os.environ["SLACK_BOT_TOKEN"], signing_secret=os.environ["SLACK_SIGNING_SECRET"])

    def post(reply: Reply, say: Any, respond: Any | None = None) -> None:
        if reply.replace and respond:
            respond(text=reply.text, blocks=reply.blocks or None, replace_original=True)
        else:
            say(text=reply.text, blocks=reply.blocks or None)
        if reply.snippet:
            say(text="```" + reply.snippet + "```")

    @app.event("message")
    def on_message(event: dict[str, Any], say: Any) -> None:
        if event.get("channel_type") == "im" and not event.get("bot_id"):
            post(bot.handle_dm(event["user"], event.get("text", "")), say)

    for aid in (
        bk.ACTION_SEND,
        bk.ACTION_EDIT,
        bk.ACTION_DISCARD,
        bk.ACTION_MANUAL,
        bk.ACTION_JIRA,
        *cbk.COMMAND_ACTIONS,
    ):

        @app.action(aid)
        def on_action(
            ack: Any, body: dict[str, Any], respond: Any, client: Any, say: Any, action: dict[str, Any]
        ) -> None:
            ack()
            reply = bot.handle_action(body["user"]["id"], action["action_id"], action["value"])
            if reply.modal:
                client.views_open(trigger_id=body["trigger_id"], view=reply.modal)
            else:
                post(reply, say, respond)

    @app.view(bk.MODAL_CALLBACK)
    def on_submit(ack: Any, body: dict[str, Any], view: dict[str, Any], client: Any) -> None:
        reply = bot.handle_modal_submit(body["user"]["id"], view["state"], view.get("private_metadata", ""))
        ack(**(reply.response or {}))
        if not reply.response:
            client.chat_postMessage(channel=body["user"]["id"], text=reply.text, blocks=reply.blocks)

    @app.view(cbk.MODAL_CALLBACK)
    def on_command_submit(ack: Any, body: dict[str, Any], view: dict[str, Any], client: Any) -> None:
        reply = bot.handle_command_modal_submit(
            body["user"]["id"], view["state"], view.get("private_metadata", "")
        )
        ack(**(reply.response or {}))
        if not reply.response:
            client.chat_postMessage(channel=body["user"]["id"], text=reply.text, blocks=reply.blocks)

    @app.command("/tt")
    def on_slash(ack: Any, command: dict[str, Any], respond: Any, client: Any = None) -> None:
        ack()
        r = bot.handle_slash(command["user_id"], command.get("text", ""))
        if r.modal and client is not None:
            client.views_open(trigger_id=command["trigger_id"], view=r.modal)
            return
        respond(text=r.text, blocks=r.blocks or None, response_type="ephemeral")

    return app


def main() -> None:  # pragma: no cover - needs a Slack workspace
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    SocketModeHandler(build_app(), os.environ["SLACK_APP_TOKEN"]).start()
