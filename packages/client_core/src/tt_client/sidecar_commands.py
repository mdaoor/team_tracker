"""JSON-in / JSON-out command verbs for the desktop and mobile sidecar (ADR-0038: no business logic in Rust or TypeScript).

The Tauri shell forwards `tt-sidecar <verb> '<json args>'`; the PyInstaller entry point calls `dispatch(verb, args, ctx)` for the
verbs below and prints the returned dict. Everything the palette needs to propose, preview, confirm and send a management command
lives here, on top of `command_flow`, so the desktop behaves exactly like the CLI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tt_schema.client import ClientCommand

from tt_client import forms, roles
from tt_client.api import ApiError, Unreachable
from tt_client.catalogue import Catalogue, CatalogueCache
from tt_client.command_flow import (
    GatewayCommandTranslator,
    Outcome,
    apply_force,
    plan_confirmation,
    preview_commands,
    propose,
    stage,
)
from tt_client.commands import strip_meta
from tt_client.outbox import Outbox, sync
from tt_client.pack import ContextPack


@dataclass
class CommandContext:
    api: Any
    outbox: Outbox
    catalogue_cache: CatalogueCache
    role_cache: roles.RoleCache
    pack: Callable[[], ContextPack | None]
    actor: str
    workspace: str
    now: Callable[[], datetime]
    tz: str = "UTC"
    translator: GatewayCommandTranslator | None = None
    get_role: Callable[[], str] = lambda: roles.DEFAULT_MODE
    set_role: Callable[[str], None] = lambda _m: None


VERBS = (
    "cmd-state", "cmd-role", "cmd-propose", "cmd-fields", "cmd-build", "cmd-preview", "cmd-send", "cmd-queue",
    "cmd-resolve", "cmd-team-now",
)  # fmt: skip


def _catalogue(ctx: CommandContext) -> Catalogue | None:
    return ctx.catalogue_cache.refresh(ctx.api)


def _cmd_doc(c: ClientCommand) -> dict[str, Any]:
    return c.model_dump(mode="json", exclude_none=True)


def _commands(raw: list[dict[str, Any]]) -> list[ClientCommand]:
    return [ClientCommand.model_validate(c) for c in raw]


def _outcome_doc(o: Outcome) -> dict[str, Any]:
    return {
        "client_id": o.command.client_id,
        "op": o.command.op,
        "args": strip_meta(o.command.args),
        "on_behalf_of": o.command.on_behalf_of,
        "status": o.status,
        "rows": [r.to_dict() for r in o.rows],
        "summary": o.summary,
        "errors": o.errors,
        "destructive": o.destructive,
    }


def _state(ctx: CommandContext) -> dict[str, Any]:
    """Role switch + verb chips + the ops the role surfaces (the palette header renders this)."""
    allowed = ctx.role_cache.refresh(ctx.api)
    role = ctx.get_role()
    if role not in allowed:
        role = roles.DEFAULT_MODE
    cat = _catalogue(ctx)
    base: dict[str, Any] = {
        "role": role,
        "modes": [{"mode": m, "label": roles.label(m)} for m in allowed],
        "ops": [],
        "chips": [],
        "offline_catalogue": cat is None,
    }
    if cat is None:
        return base
    s = roles.surface(role, cat)
    base["chips"] = [c.to_dict() for c in s.chips]
    base["ops"] = [
        {"op": o.op, "group": o.group, "description": o.description, "destructive": o.destructive,
         "preview": o.preview, "first": o in s.first}
        for o in s.visible
    ]  # fmt: skip
    pack = ctx.pack()
    base["review_queue_count"] = pack.review_queue_count if pack else 0
    base["pending_approvals"] = pack.pending_approvals if pack else []
    return base


def dispatch(verb: str, args: dict[str, Any], ctx: CommandContext) -> dict[str, Any]:
    if verb == "cmd-state":
        return _state(ctx)
    if verb == "cmd-role":
        allowed = ctx.role_cache.refresh(ctx.api)
        try:
            chosen = roles.resolve_mode(str(args.get("mode", "")), allowed)
        except ValueError as exc:
            return {"error": str(exc)}
        ctx.set_role(chosen)
        return _state(ctx)
    cat = _catalogue(ctx)
    if cat is None:
        return {"error": "no command catalogue yet: connect once to fetch it"}
    pack = ctx.pack()
    role = ctx.get_role()
    if verb == "cmd-propose":
        prop = propose(
            str(args.get("text", "")), cat=cat, pack=pack, actor=ctx.actor, now=ctx.now(), tz=ctx.tz,
            role_mode=role, translator=ctx.translator,
        )  # fmt: skip
        return {
            "commands": [_cmd_doc(c) for c in prop.commands],
            "source": prop.source,
            "notes": prop.notes,
            "unmatched": prop.unmatched,
            "incomplete": [
                {
                    "op": i.op,
                    "args": i.args,
                    "issues": i.issues,
                    "form": forms.fields_spec(cat.require(i.op), pack),
                }
                for i in prop.incomplete
            ],
        }
    if verb == "cmd-fields":
        meta = cat.get(str(args.get("op", "")))
        return forms.fields_spec(meta, pack) if meta else {"error": f"unknown op {args.get('op')!r}"}
    if verb == "cmd-build":
        meta = cat.get(str(args.get("op", "")))
        if meta is None:
            return {"error": f"unknown op {args.get('op')!r}"}
        try:
            cmd = forms.build_command(
                meta, dict(args.get("answers") or {}), catalogue=cat, pack=pack, actor=ctx.actor,
                now=ctx.now(), role_mode=role, mode="manual", on_behalf_of=args.get("for"),
            )  # fmt: skip
        except forms.FormError as exc:
            return {"errors": [{"pointer": i.pointer, "message": i.message} for i in exc.issues]}
        return {"command": _cmd_doc(cmd)}
    if verb == "cmd-preview":
        outcomes = preview_commands(ctx.api, cat, _commands(args.get("commands", [])))
        return {"outcomes": [_outcome_doc(o) for o in outcomes]}
    if verb == "cmd-send":
        return _send(ctx, cat, args)
    if verb == "cmd-queue":
        try:
            return {"rows": ctx.api.review_queue()}
        except (Unreachable, ApiError) as exc:
            return {"error": str(exc)}
    if verb == "cmd-resolve":
        meta = cat.get("review.resolve")
        if meta is None:
            return {"error": "this server does not offer review.resolve"}
        cmds = [
            forms.build_command(
                meta,
                {"row": r["id"], "choice": r["choice"]},
                catalogue=cat,
                pack=pack,
                actor=ctx.actor,
                now=ctx.now(),
                role_mode=role,
                mode="manual",
            )  # fmt: skip
            for r in args.get("rows", [])
        ]
        return _send(ctx, cat, {"commands": [_cmd_doc(c) for c in cmds], "confirmed": True})
    if verb == "cmd-team-now":
        try:
            return {"activities": ctx.api.team_now()}
        except (Unreachable, ApiError) as exc:
            return {"error": str(exc)}
    return {"error": f"unknown verb {verb!r}"}


def _send(ctx: CommandContext, cat: Catalogue, args: dict[str, Any]) -> dict[str, Any]:
    """Confirmation is enforced HERE, not in the UI: an ordinary command needs `confirmed`; a destructive one also needs
    `second_confirmed` (or `force`, which is logged as `_meta.forced`)."""
    cmds = _commands(args.get("commands", []))
    if not cmds:
        return {"error": "no commands to send"}
    force = bool(args.get("force"))
    plan = plan_confirmation(cat, cmds, yes=bool(args.get("confirmed")), force=force)
    if not (args.get("confirmed") or force):
        return {"status": "needs_confirmation"}
    if plan.destructive and not (force or args.get("second_confirmed")):
        return {"status": "needs_second_confirmation", "destructive": plan.destructive}
    cmds = apply_force(cat, cmds, force)
    entry = stage(ctx.outbox, ctx.workspace, cmds, mode=str(args.get("mode", "assisted")))
    report = sync(ctx.api, ctx.outbox, only=[entry])
    if report.offline:
        return {"status": "queued", "kept": report.kept}
    if report.quarantined:
        return {"status": "failed", "error": "the server rejected the file (schema violation)"}
    rejected = [r for r in report.results or [] if r.get("status") == "rejected"]
    return {
        "status": "rejected" if rejected else "sent",
        "results": report.results or [],
        "rejected": [{"client_id": r["client_id"], "errors": r.get("errors", [])} for r in rejected],
    }
