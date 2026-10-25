"""Management commands from Slack (Phase 14): `/tt do …`, `/tt cmd <op>` (modal form), approvals and waives as buttons.

Transport-free like `SlackBot`: every method takes plain values and returns a `Reply`. It leans on `tt_client.command_flow`, so a
command card, its Effect preview and its confirmation rules are the same as in the CLI and the desktop palette:

* an ordinary command: one Confirm click;
* a destructive one: the Confirm button carries Slack's native confirmation dialog, and the handler refuses a click that did not
  come through it (`second`), so there is no way to merge / remove / re-line from Slack with a single stray click;
* approvals and waives are message buttons (one click decides; a waive asks for its reason in a one-field modal)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from tt_client import forms, roles
from tt_client.catalogue import Catalogue
from tt_client.command_flow import (
    Outcome,
    plan_confirmation,
    preview_commands,
    propose,
    stage,
)
from tt_client.commands import strip_meta
from tt_client.outbox import sync
from tt_schema.client import ClientCommand

from tt_slack import command_blocks as cb

if TYPE_CHECKING:
    from tt_slack.bot import Reply, SlackBot

SLASH = ("do", "cmd", "role", "approvals", "waive", "ops")


class CommandHandlers:
    def __init__(self, bot: SlackBot) -> None:
        self.bot = bot
        self.roles: dict[
            str, str
        ] = {}  # slack user -> chosen role mode (a lens; the server still authorises)

    # ------------------------------------------------------------------ helpers
    def _reply(self, **kw: Any) -> Reply:
        from tt_slack.bot import Reply

        return Reply(**kw)

    def _ctx(self, slack_user: str) -> tuple[Any, Catalogue | None, str, Reply | None]:
        rt, need_link = self.bot._rt(slack_user)  # noqa: SLF001 - the bot owns linking
        if rt is None:
            return None, None, "", need_link
        cache = getattr(rt, "catalogue_cache", None)
        cat = cache.refresh(rt.api) if cache is not None else None
        if cat is None:
            return (
                rt,
                None,
                "",
                self._reply(
                    text="I have no command catalogue yet (the server was not reachable). Try again in a moment.",
                    ephemeral=True,
                ),
            )
        return rt, cat, self.role_of(slack_user, rt), None

    def role_of(self, slack_user: str, rt: Any) -> str:
        try:
            return roles.normalise(
                str(self.roles.get(slack_user) or getattr(rt.cfg, "role_mode", "reporter"))
            )
        except ValueError:
            return roles.DEFAULT_MODE

    @staticmethod
    def _now(rt: Any) -> datetime:
        return rt.parse_ctx().now  # type: ignore[no-any-return]

    def _card(
        self,
        rt: Any,
        cat: Catalogue,
        role: str,
        commands: list[ClientCommand],
        *,
        mode: str,
        replace: bool = False,
    ) -> Reply:
        outcomes = preview_commands(rt.api, cat, commands)
        entry = stage(rt.outbox, rt.workspace, commands, mode=mode, previews=outcomes)
        perms = {
            o.command.op: m.permission for o in outcomes if (m := cat.get(o.command.op)) and m.permission
        }
        return self._reply(
            text="Command: " + ", ".join(c.op for c in commands),
            blocks=cb.command_card(
                outcomes, entry.batch_id, role_label=f"role: {roles.label(role)}", permission=perms
            ),
            snippet=json.dumps(rt.outbox.load(entry), indent=2, ensure_ascii=False),
            replace=replace,
        )

    # ------------------------------------------------------------------ slash
    def slash(self, slack_user: str, text: str) -> Reply:
        parts = text.strip().split(None, 1)
        verb, rest = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
        if verb == "do":
            return self.do(slack_user, rest)
        if verb == "cmd":
            return self.open_form(slack_user, rest.strip())
        if verb == "role":
            return self.role(slack_user, rest.strip())
        if verb == "approvals":
            return self.approvals(slack_user)
        if verb == "waive":
            return self.waive(slack_user, rest)
        return self.ops(slack_user)

    def do(self, slack_user: str, text: str) -> Reply:
        rt, cat, role, err = self._ctx(slack_user)
        if err or cat is None:
            return err  # type: ignore[return-value]
        if not text.strip():
            return self._reply(
                text="Tell me what to do, e.g. `/tt do merge WEB-101 and WEB-105` or `/tt do tag DATA-1 DATA-2 #q4`.",
                ephemeral=True,
            )
        translator = rt.command_translator() if hasattr(rt, "command_translator") else None
        pack = rt.pack(refresh=False)
        prop = propose(text, cat=cat, pack=pack, actor=rt.cfg.handle, now=self._now(rt), tz=getattr(rt.cfg, "time_zone", "UTC"), role_mode=role, translator=translator)  # fmt: skip
        if (
            not prop.commands and prop.incomplete
        ):  # understood but a required value is missing: ask with a generated form
            inc = prop.incomplete[0]
            return self._reply(
                modal=cb.command_modal(inc.op, forms.fields_for(cat.require(inc.op), pack), answers=inc.args)
            )
        if not prop.commands:
            return self._reply(
                text="I could not turn that into a command. `/tt cmd <op>` opens a form; `/tt ops` lists what is available.",
                ephemeral=True,
            )
        reply = self._card(rt, cat, role, prop.commands, mode="assisted")
        if prop.notes:
            reply.blocks.append(
                {"type": "context", "elements": [{"type": "mrkdwn", "text": "; ".join(prop.notes)[:2900]}]}
            )
        return reply

    def open_form(self, slack_user: str, op: str) -> Reply:
        rt, cat, role, err = self._ctx(slack_user)
        if err or cat is None:
            return err  # type: ignore[return-value]
        meta = cat.get(op)
        if meta is None:
            s = roles.surface(role, cat)
            return self._reply(
                text="Unknown op. Available: " + ", ".join(o.op for o in s.visible[:20]), ephemeral=True
            )
        return self._reply(modal=cb.command_modal(op, forms.fields_for(meta, rt.pack(refresh=False))))

    def role(self, slack_user: str, arg: str) -> Reply:
        rt, err = self.bot._rt(slack_user)  # noqa: SLF001
        if rt is None:
            return err  # type: ignore[return-value]
        role_cache = getattr(rt, "role_cache", None)
        allowed = role_cache.refresh(rt.api) if role_cache is not None else [roles.DEFAULT_MODE]
        cur = self.role_of(slack_user, rt)
        if not arg:
            return self._reply(
                text="\n".join(f"{'*' if m == cur else ' '} {m}: {roles.describe(m)}" for m in allowed),
                ephemeral=True,
            )
        try:
            chosen = roles.resolve_mode(arg, allowed)
        except ValueError as exc:
            return self._reply(text=str(exc), ephemeral=True)
        self.roles[slack_user] = chosen
        return self._reply(text=f"Role: {roles.label(chosen)}", ephemeral=True)

    def ops(self, slack_user: str) -> Reply:
        rt, cat, role, err = self._ctx(slack_user)
        if err or cat is None:
            return err  # type: ignore[return-value]
        s = roles.surface(role, cat)
        lines = [f"`{o.op}` {o.description}" + (" :warning:" if o.destructive else "") for o in s.visible]
        chips = ", ".join(c.label for c in s.chips)
        return self._reply(
            text=f"*{roles.label(role)}* — {len(s.visible)} verbs"
            + (f"  ({chips})" if chips else "")
            + "\n"
            + "\n".join(lines),
            ephemeral=True,
        )

    def approvals(self, slack_user: str) -> Reply:
        rt, cat, role, err = self._ctx(slack_user)
        if err or cat is None:
            return err  # type: ignore[return-value]
        pack = rt.pack(refresh=False)
        pending = pack.pending_approvals if pack else []
        if not pending:
            return self._reply(text="No pending approvals.", ephemeral=True)
        blocks: list[dict[str, Any]] = []
        for a in pending:
            blocks += cb.approval_message(a)
        return self._reply(text=f"{len(pending)} pending approval(s)", blocks=blocks, ephemeral=True)

    def waive(self, slack_user: str, rest: str) -> Reply:
        vid, _, reason = rest.strip().partition(" ")
        if not vid:
            return self._reply(text="`/tt waive V-9 because holiday week`", ephemeral=True)
        reason = reason.removeprefix("because").strip(" :-")
        if not reason:
            return self._reply(modal=cb.waive_modal(vid.upper()))
        return self.do(slack_user, f"waive violation {vid} because {reason}")

    # ------------------------------------------------------------------ buttons
    def handle_action(self, slack_user: str, action_id: str, value: str) -> Reply:
        rt, cat, role, err = self._ctx(slack_user)
        if err or cat is None:
            return err  # type: ignore[return-value]
        if action_id in (cb.ACTION_APPROVE, cb.ACTION_REJECT):
            return self._decide(
                rt, cat, role, value, "approve" if action_id == cb.ACTION_APPROVE else "reject"
            )
        if action_id == cb.ACTION_WAIVE:
            return self._reply(modal=cb.waive_modal(value))
        info = json.loads(value) if value.startswith("{") else {"batch": value}
        try:
            entry = rt.outbox.find(info["batch"])
        except Exception:  # noqa: BLE001 - find raises its own not-found/ambiguous errors
            return self._reply(
                text="That card is no longer available (already sent or discarded).", ephemeral=True
            )
        if action_id == cb.ACTION_DISCARD:
            rt.outbox.discard(entry)
            return self._reply(text="Discarded.", replace=True)
        doc = rt.outbox.load(entry)
        cmds = [ClientCommand.model_validate(c) for c in doc.get("commands", [])]
        if action_id == cb.ACTION_EDIT:
            first = cmds[0]
            return self._reply(
                modal=cb.command_modal(
                    first.op,
                    forms.fields_for(cat.require(first.op), rt.pack(refresh=False)),
                    batch_id=entry.batch_id,
                    answers=strip_meta(first.args),
                )
            )
        if action_id == cb.ACTION_CONFIRM:
            plan = plan_confirmation(cat, cmds, yes=True, force=False)
            if plan.destructive and not info.get("second"):
                return self._reply(
                    text=":warning: destructive command: confirm it in the dialog the button opens.",
                    ephemeral=True,
                )
            return self._send(rt, entry)
        return self._reply(text="Unknown action.", ephemeral=True)

    def _send(self, rt: Any, entry: Any) -> Reply:
        doc = rt.outbox.load(entry)
        report = sync(rt.api, rt.outbox, only=[entry])
        if report.offline:
            return self._reply(
                text=f"Offline - queued {report.kept} update(s); they will be sent when the server is back.",
                replace=True,
            )
        if report.quarantined:
            return self._reply(
                text="The server rejected that file (schema violation); it was set aside.", ephemeral=True
            )
        return self._reply(text=result_message(report.results or [], doc), replace=True)

    def _decide(self, rt: Any, cat: Catalogue, role: str, approval_id: str, decision: str) -> Reply:
        meta = cat.get("approval.decide")
        if meta is None:
            return self._reply(text="This server does not offer approvals from the client.", ephemeral=True)
        cmd = forms.build_command(meta, {"approval": approval_id, "decision": decision}, catalogue=cat, pack=rt.pack(refresh=False), actor=rt.cfg.handle, now=self._now(rt), role_mode=role, mode="manual")  # fmt: skip
        entry = stage(rt.outbox, rt.workspace, [cmd], mode="manual")
        return self._send(rt, entry)

    # ------------------------------------------------------------------ modal
    def submit_modal(self, slack_user: str, view_state: dict[str, Any], private_metadata: str) -> Reply:
        rt, cat, role, err = self._ctx(slack_user)
        if err or cat is None:
            return err  # type: ignore[return-value]
        meta_info = json.loads(private_metadata or "{}")
        op = meta_info.get("op", "")
        meta = cat.get(op)
        if meta is None:
            return self._reply(text="That form is no longer valid.", ephemeral=True)
        answers = {**cb.command_modal_values(view_state), **meta_info.get("fixed", {})}
        try:
            cmd = forms.build_command(meta, answers, catalogue=cat, pack=rt.pack(refresh=False), actor=rt.cfg.handle, now=self._now(rt), role_mode=role, mode="manual")  # fmt: skip
        except forms.FormError as exc:
            names = {f.name for f in forms.fields_for(meta)}
            errors: dict[str, str] = {}
            for i in exc.issues:
                name = i.pointer.strip("/").split("/")[0]
                errors.setdefault(name if name in names else next(iter(names), ""), i.message[:150])
            return self._reply(response=cb.modal_errors(errors))
        old = meta_info.get("batch")
        if old:
            try:
                rt.outbox.discard(rt.outbox.find(old))
            except Exception:  # noqa: BLE001, S110 - already gone
                pass
        return self._card(rt, cat, role, [cmd], mode="manual", replace=bool(old))


def result_message(results: list[dict[str, Any]], doc: dict[str, Any]) -> str:
    cmd_ids = {c["client_id"]: c["op"] for c in doc.get("commands", [])}
    lines = []
    for r in results:
        cid = r.get("client_id")
        if cid in cmd_ids:
            if r.get("status") == "rejected":
                why = "; ".join(e.get("message", e.get("code", "")) for e in r.get("errors", []))
                lines.append(f":x: `{cmd_ids[cid]}` rejected: {why}")
            else:
                lines.append(
                    f":white_check_mark: `{cmd_ids[cid]}`"
                    + (" (already applied)" if r.get("status") == "deduplicated" else "")
                )
        else:
            item = r.get("item") or {}
            lines.append(f":white_check_mark: {item.get('key', '')} {item.get('title', '')}".strip())
    return "\n".join(lines) or "Nothing was applied."


__all__ = ["SLASH", "CommandHandlers", "Outcome", "result_message"]
