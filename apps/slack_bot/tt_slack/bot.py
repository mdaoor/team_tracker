"""Slack capture logic, transport-free: every method takes plain values and returns a `Reply` (blocks to post, or a modal to open).

The reporter's JSON file stays the unit of work: a DM becomes an outbox file, the card's buttons act on that file by id, and the
thread keeps the JSON as a snippet (the chat equivalent of "your file")."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from tt_client.form import build_message, message_to_answers, missing_required, to_batch
from tt_client.outbox import Entry, Outbox, sync
from tt_client.pack import ContextPack
from tt_client.parser.llm import LLMTranslator, translate_line
from tt_client.parser.structured import ParseContext
from tt_client.preview import preview_dict

from tt_slack import blocks as bk
from tt_slack import command_blocks as cbk
from tt_slack.commands import SLASH as COMMAND_SLASH
from tt_slack.commands import CommandHandlers
from tt_slack.linking import Link, LinkStore


class Runtime(Protocol):
    """What the bot needs per linked user (the CLI's `Runtime` satisfies it)."""

    outbox: Outbox
    api: Any

    def pack(self, *, refresh: bool = True) -> ContextPack | None: ...
    def translator(self) -> LLMTranslator: ...
    def parse_ctx(self) -> ParseContext: ...
    @property
    def workspace(self) -> str: ...


@dataclass
class Reply:
    text: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)
    modal: dict[str, Any] | None = None
    response: dict[str, Any] | None = None  # e.g. a modal `response_action: errors`
    snippet: str | None = None  # the batch JSON, posted into the thread
    ephemeral: bool = False
    replace: bool = False  # update the card the user clicked instead of posting a new one


class SlackBot:
    def __init__(
        self, links: LinkStore, runtime_for: Callable[[Link, str], Runtime], server_url: str
    ) -> None:
        self.links, self.runtime_for, self.server_url = links, runtime_for, server_url
        self.commands = CommandHandlers(self)  # Phase 14: /tt do, approvals, waives, generated forms

    # ------------------------------------------------------------------ helpers
    def _rt(self, slack_user: str) -> tuple[Runtime | None, Reply | None]:
        link = self.links.get(slack_user)
        if link is None:
            url = self.links.deep_link(self.server_url, slack_user)
            return None, Reply(
                text=f"Link your Team Tracker account first: {url}",
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"Link your account to start: <{url}|Sign in to Team Tracker>"}}],
                ephemeral=True,
            )  # fmt: skip
        return self.runtime_for(link, slack_user), None

    @staticmethod
    def _entry(rt: Runtime, batch_id: str) -> Entry | None:
        try:
            return rt.outbox.find(batch_id)
        except Exception:  # noqa: BLE001 - find raises its own not-found/ambiguous errors
            return None

    def _card(
        self, rt: Runtime, batch: dict[str, Any], entry: Entry, pack: ContextPack | None, source: str
    ) -> Reply:
        msgs = [m for m in batch["messages"]]
        from tt_schema.client import ClientMessage

        parsed = [ClientMessage.model_validate(m) for m in msgs]
        cards = preview_dict(parsed, pack)["cards"]
        has_jira = {p["key"] for p in (pack.data.get("projects", []) if pack else []) if p.get("jira")}
        projects = {(m.project or "").upper() for m in parsed}
        jira: bool | None = None
        if projects & has_jira:
            jira = any(m.jira and m.jira.get("send") for m in parsed)
        return Reply(
            text="Parsed your update",
            blocks=bk.card(cards, entry.batch_id, jira=jira, source=source),
            snippet=json.dumps(batch, indent=2, ensure_ascii=False),
        )

    # ------------------------------------------------------------------ entry points
    def handle_dm(self, slack_user: str, text: str, *, now: datetime | None = None) -> Reply:
        rt, need_link = self._rt(slack_user)
        if rt is None:
            return need_link  # type: ignore[return-value]
        if not text.strip():
            return Reply(text="Tell me what you did, e.g. `fixed login loop, took 2h`", ephemeral=True)
        pack = rt.pack()
        ctx = rt.parse_ctx()
        if now:
            ctx.now = now
        t = translate_line(text, ctx, pack=pack, translator=rt.translator())
        if not t.batch.messages:
            return Reply(text="I could not find anything to record in that.", ephemeral=True)
        batch = t.batch.model_copy(update={"workspace": rt.workspace}).model_dump(
            mode="json", exclude_none=True
        )
        entry = rt.outbox.write(batch, preview_dict(t.batch.messages, pack))
        return self._card(rt, batch, entry, pack, t.source)

    def handle_action(self, slack_user: str, action_id: str, value: str) -> Reply:
        rt, need_link = self._rt(slack_user)
        if rt is None:
            return need_link  # type: ignore[return-value]
        if action_id in cbk.COMMAND_ACTIONS:
            return self.commands.handle_action(slack_user, action_id, value)
        batch_id = json.loads(value)["batch"] if action_id == bk.ACTION_JIRA else value
        entry = self._entry(rt, batch_id)
        if entry is None:
            return Reply(text="That card is no longer available (already sent or discarded).", ephemeral=True)
        if action_id == bk.ACTION_DISCARD:
            rt.outbox.discard(entry)
            return Reply(text="Discarded.", replace=True)
        if action_id in (bk.ACTION_EDIT, bk.ACTION_MANUAL):
            pack = rt.pack(refresh=False)
            if pack is None:
                return Reply(text="No context pack yet - try again in a moment.", ephemeral=True)
            first = rt.outbox.load(entry)["messages"][0]
            from tt_schema.client import ClientMessage

            answers = (
                message_to_answers(ClientMessage.model_validate(first)) if action_id == bk.ACTION_EDIT else {}
            )
            return Reply(modal=bk.manual_modal(pack, answers, batch_id=entry.batch_id))
        if action_id == bk.ACTION_JIRA:
            return self._set_jira(rt, entry, bool(json.loads(value)["on"]))
        if action_id == bk.ACTION_SEND:
            report = sync(rt.api, rt.outbox, only=[entry])
            if report.offline:
                return Reply(
                    text=f"Offline - queued {report.kept} update(s); they will be sent when the server is back.",
                    replace=True,
                )
            if report.quarantined:
                return Reply(
                    text="The server rejected that file (schema violation); it was set aside.", ephemeral=True
                )
            return Reply(text=bk.result_message(report.results or []), replace=True)
        return Reply(text="Unknown action.", ephemeral=True)

    def _set_jira(self, rt: Runtime, entry: Entry, on: bool) -> Reply:
        batch = rt.outbox.load(entry)
        pack = rt.pack(refresh=False)
        jira_cfg = {p["key"]: p for p in (pack.data.get("projects", []) if pack else []) if p.get("jira")}
        for m in batch["messages"]:
            cfg = jira_cfg.get((m.get("project") or "").upper())
            if cfg is None:
                continue  # the chip only exists for projects with Jira; never invent a target
            m["jira"] = (
                {"project": cfg["jira"], "send": True} if on else {"project": cfg["jira"], "send": False}
            )
        rt.outbox.discard(entry)
        new = rt.outbox.write(batch, None)
        out = self._card(rt, batch, new, pack, "structured")
        out.replace = True
        return out

    def handle_modal_submit(
        self, slack_user: str, view_state: dict[str, Any], batch_id: str, *, now: datetime | None = None
    ) -> Reply:
        rt, need_link = self._rt(slack_user)
        if rt is None:
            return need_link  # type: ignore[return-value]
        pack = rt.pack(refresh=False)
        if pack is None:
            return Reply(text="No context pack yet - try again in a moment.", ephemeral=True)
        answers = bk.modal_values(view_state)
        missing = missing_required(answers, pack)
        if missing:  # the same client-side rule as the CLI form; the server would still accept any valid file
            return Reply(response=bk.modal_errors(missing))
        cfg = getattr(rt, "cfg", None)
        msg = build_message(
            answers, actor=getattr(cfg, "handle", "") or "@me", now=now or datetime.now(UTC),
            tz=getattr(cfg, "time_zone", "UTC"), hours_per_day=getattr(cfg, "hours_per_day", 8),
        )  # fmt: skip
        batch = to_batch([msg], rt.workspace).model_dump(mode="json", exclude_none=True)
        if batch_id and (old := self._entry(rt, batch_id)):
            rt.outbox.discard(old)
        entry = rt.outbox.write(batch, preview_dict([msg], pack))
        out = self._card(rt, batch, entry, pack, "manual")
        out.replace = bool(batch_id)
        return out

    def handle_slash(self, slack_user: str, text: str) -> Reply:
        rt, need_link = self._rt(slack_user)
        if rt is None:
            return need_link  # type: ignore[return-value]
        cmd = (text.strip().split() or ["help"])[0].lower()
        if cmd == "now":
            acts = rt.api.active_activities()
            if not acts:
                return Reply(text="Nothing in progress right now.", ephemeral=True)
            return Reply(
                text="\n".join(f":hourglass: {a.get('key', '')} {a.get('title', '')}" for a in acts),
                ephemeral=True,
            )
        if cmd == "today":
            logs = rt.api.daily_logs()
            if not logs:
                return Reply(text="No daily log yet.", ephemeral=True)
            return Reply(
                text=logs[0].get("summary") or "(empty log)", blocks=self._log_blocks(logs[0]), ephemeral=True
            )
        if cmd == "unlink":
            return Reply(
                text="Unlinked." if self.links.unlink(slack_user) else "You were not linked.", ephemeral=True
            )
        if cmd in COMMAND_SLASH:
            return self.commands.slash(slack_user, text)
        return Reply(
            text="Send me a DM with what you did, or use `/tt now`, `/tt today`, `/tt do …`, `/tt cmd <op>`, `/tt approvals`, `/tt unlink`.",
            ephemeral=True,
        )

    def handle_command_modal_submit(
        self, slack_user: str, view_state: dict[str, Any], private_metadata: str
    ) -> Reply:
        return self.commands.submit_modal(slack_user, view_state, private_metadata)

    @staticmethod
    def _log_blocks(log: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Your day*\n{log.get('summary') or ''}"}}
        ]
        if log.get("status") != "confirmed":
            out.append({"type": "actions", "elements": [{"type": "button", "action_id": "tt_confirm_log", "style": "primary",
                                                         "text": {"type": "plain_text", "text": "Confirm"}, "value": log["id"]}]})  # fmt: skip
        return out

    def handle_confirm_log(self, slack_user: str, log_id: str) -> Reply:
        rt, need_link = self._rt(slack_user)
        if rt is None:
            return need_link  # type: ignore[return-value]
        rt.api.confirm_daily_log(log_id)
        return Reply(text="Confirmed. Thanks!", replace=True)

    def end_of_day(self, slack_user: str) -> Reply | None:
        """The one DM a day. Nothing when the log is missing or already confirmed (never nag)."""
        rt, _ = self._rt(slack_user)
        if rt is None:
            return None
        logs = rt.api.daily_logs()
        if not logs or logs[0].get("status") == "confirmed":
            return None
        return Reply(text="Time to wrap up your day", blocks=self._log_blocks(logs[0]))
