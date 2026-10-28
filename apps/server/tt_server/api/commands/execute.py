"""Run one `ClientCommand`: validate, authorise, preview or apply, record. Rejection is a per-command result, never an HTTP error.

Each command is its own transaction. The unit of work is put on hold while a handler runs, so the router functions it calls
(which each `commit()`) only flush; the executor makes the single commit after the handler, with the command's `CommandLog` row
and `command.applied` event. On any failure the session is rolled back and the event buffer cleared, so a rejected command
leaves nothing behind (not even the first steps of a multi-step op) and does not disturb the commands around it in a batch.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, StatementError
from tt_schema.base import new_id
from tt_schema.client import ClientCommand

from tt_server.acl.permissions import TOKEN_SCOPES
from tt_server.api.authz import check, check_item, check_project
from tt_server.api.commands.registry import REGISTRY, Command, Ctx
from tt_server.api.deps import Actor, Uow
from tt_server.api.errors import ApiError
from tt_server.db import models as m
from tt_server.domain.common import emit_admin
from tt_server.domain.errors import DomainError

log = structlog.get_logger()
META = "_meta"
MANAGE_LEVEL = TOKEN_SCOPES.index("manage")


def _err(pointer: str, code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"pointer": pointer, "code": code, "message": message, **({"details": details} if details else {})}


def _result(cmd: ClientCommand, status: str, *, result: Any = None, preview: Any = None, errors: list[dict[str, Any]] | None = None, dedup: bool = False) -> dict[str, Any]:  # fmt: skip
    out: dict[str, Any] = {"client_id": cmd.client_id, "op": cmd.op, "status": status, "result": jsonable_encoder(result), "preview": jsonable_encoder(preview), "errors": errors or []}  # fmt: skip
    if dedup:
        out["deduplicated"] = True
    return out


def scope_level(actor: Actor) -> int:
    scopes = actor.acl.token_scopes
    if scopes is None:
        return len(TOKEN_SCOPES)  # an interactive session carries the person's full authority
    return max((TOKEN_SCOPES.index(s) for s in scopes if s in TOKEN_SCOPES), default=-1)


def can_send_commands(actor: Actor) -> bool:
    return scope_level(actor) >= MANAGE_LEVEL


async def _authorise(ctx: Ctx, spec: Command, args: Any) -> None:
    """The op's declared permission at its declared scope. Invisible objects are 404 (raised by the lookups), missing rights 403."""
    if spec.permission is None:
        return
    actor, uow = ctx.actor, ctx.uow
    if spec.scope == "workspace" or spec.scope_arg is None:
        check(actor, spec.permission)
        return
    from tt_server.api.routers import core

    ref = getattr(args, spec.scope_arg, None)
    if spec.scope == "project":
        check(actor, spec.permission) if ref is None else await check_project(actor, uow, spec.permission, await core._project(uow, actor.ws.id, ref))  # fmt: skip
    elif spec.scope == "target":
        # a classification/link target of any type: the item's, project's or product's own scope (never "an item key")
        from tt_schema.enums import TargetType

        from tt_server.api.overlay_support import check_target, target_id_of

        ttype = TargetType(getattr(args, "target_type", TargetType.WORK_ITEM))
        await check_target(
            actor, uow, spec.permission, ttype, await target_id_of(uow, actor.ws.id, ttype, str(ref))
        )
    elif spec.scope == "risk":
        # a risk is written in its own project: `risk.write` there, not workspace-wide
        from tt_server.domain.overlay import risk_register

        risk = await risk_register.get_risk(uow, actor.ws.id, str(ref))
        await check_project(actor, uow, spec.permission, await risk_register.project_of(uow, risk))
    elif spec.scope in ("item", "items"):
        refs = [ref] if isinstance(ref, str) else list(ref or [])
        for r in refs:
            await check_item(actor, uow, spec.permission, await core._item(uow, actor.ws.id, r))
        if not refs:
            check(actor, spec.permission)


def _summary(exc: Exception) -> list[dict[str, Any]]:
    if isinstance(exc, ValidationError):
        return [
            _err("/args/" + "/".join(str(p) for p in e["loc"]), "invalid", e["msg"]) for e in exc.errors()
        ]
    if isinstance(exc, ApiError | DomainError):
        return [_err(exc.pointer or "/args", exc.code, exc.message, **exc.details)]
    if isinstance(exc, IntegrityError):
        log.warning("command_integrity_error", error=str(exc.orig)[:300])
        return [
            _err(
                "/args",
                "conflict",
                "that conflicts with something that already exists or refers to something missing",
            )
        ]
    if isinstance(exc, StatementError):
        return [_err("/args", "invalid_parameter", "a value this field cannot hold")]
    raise exc


async def _reset(uow: Uow, actor: Actor) -> None:
    """Forget everything a failed command did (rows and buffered events). A rollback expires every loaded row, including the
    actor's, which the next command in the batch still needs: reload them."""
    await uow.session.rollback()
    for row in (actor.ws, actor.member, actor.person):
        await uow.session.refresh(row)
    uow.events._buffer.clear()  # noqa: SLF001 - the writer has no public reset; this is the one caller
    uow._touched_projection = False  # noqa: SLF001
    uow._touched_tables = set()  # noqa: SLF001
    uow._modified_columns = {}  # noqa: SLF001


async def run(
    uow: Uow, actor: Actor, cmd: ClientCommand, *, correlation_id: str, forced_preview: bool = False
) -> dict[str, Any]:
    """Execute (or preview) one command and return its result dict. Never raises for a user-level failure.

    A rejected command that was meant to be applied is audited (`_audit_rejection`); a preview is never logged."""
    res = await _run(uow, actor, cmd, correlation_id=correlation_id, forced_preview=forced_preview)
    if res["status"] == "rejected" and not (cmd.preview_only or forced_preview):
        if not any(
            e["code"] == "client_id_in_use" for e in res["errors"]
        ):  # that row is someone else's: leave it alone
            await _audit_rejection(uow, actor, cmd, res, correlation_id)
    return res


async def _audit_rejection(
    uow: Uow, actor: Actor, cmd: ClientCommand, res: dict[str, Any], correlation_id: str
) -> None:
    """Record a refused command (ADR-0056 §13): a `command_log` row with status `rejected` and a `command.rejected` event, one
    transaction. It is excluded from idempotency (only `applied` rows dedupe), so a corrected retry with the same `client_id`
    is evaluated and, if it succeeds, replaces the row. The row belongs to the authenticated actor, not to the claimed one, and
    the event names the op and error codes but never the arguments."""
    try:
        existing = await uow.session.scalar(select(m.CommandLog).where(m.CommandLog.workspace_id == actor.ws.id, m.CommandLog.client_id == cmd.client_id))  # fmt: skip
        if existing is not None and (existing.status != "rejected" or existing.actor_id != actor.person.id):
            return  # an applied command (or another person's row) is never overwritten by a later refusal
        uow.context["actor_id"] = actor.person.id
        uow.context.setdefault("correlation_id", correlation_id)
        if existing is None:
            uow.session.add(m.CommandLog(id=new_id(), workspace_id=actor.ws.id, client_id=cmd.client_id, actor_id=actor.person.id, op=cmd.op[:120], status="rejected", result=res))  # fmt: skip
        else:
            existing.op, existing.result = cmd.op[:120], res
        emit_admin(
            uow,
            workspace_id=actor.ws.id,
            actor_id=actor.person.id,
            event_type="command.rejected",
            payload={"after": {"op": cmd.op[:120], "client_id": cmd.client_id, "status": "rejected", "codes": [e["code"] for e in res["errors"]]}},
        )  # fmt: skip
        await uow.commit()
    except (
        IntegrityError
    ):  # a concurrent retry won the unique (workspace, client_id): the audit is best effort
        await _reset(uow, actor)


async def _run(
    uow: Uow, actor: Actor, cmd: ClientCommand, *, correlation_id: str, forced_preview: bool = False
) -> dict[str, Any]:
    spec = REGISTRY.get(cmd.op)
    if spec is None:
        return _result(
            cmd,
            "rejected",
            errors=[_err("/op", "unknown_op", f"unknown command {cmd.op!r}; see GET /commands/catalogue")],
        )
    if not can_send_commands(actor):
        return _result(
            cmd,
            "rejected",
            errors=[_err("/", "insufficient_scope", "this token may only send capture messages", status=403)],
        )
    if scope_level(actor) < TOKEN_SCOPES.index(spec.token_scope):
        return _result(
            cmd,
            "rejected",
            errors=[
                _err(
                    "/",
                    "insufficient_scope",
                    f"{cmd.op} needs a token with the {spec.token_scope!r} scope",
                    status=403,
                    required_scope=spec.token_scope,
                )
            ],
        )
    if cmd.actor.lstrip("@") != actor.handle.lstrip("@"):
        return _result(
            cmd,
            "rejected",
            errors=[
                _err("/actor", "actor_mismatch", "a command must be sent as the person who owns the token")
            ],
        )
    preview = cmd.preview_only or forced_preview
    meta = dict(cmd.args.get(META) or {})
    raw = {k: v for k, v in cmd.args.items() if k != META}

    prior: m.CommandLog | None = None  # an earlier rejection of this client_id: a corrected retry replaces it
    if not preview and (hit := await uow.session.scalar(select(m.CommandLog).where(m.CommandLog.workspace_id == actor.ws.id, m.CommandLog.client_id == cmd.client_id))):  # fmt: skip
        if hit.actor_id != actor.person.id:
            return _result(cmd, "rejected", errors=[_err("/client_id", "client_id_in_use", "that client_id was already used by someone else; send a new one")])  # fmt: skip
        if hit.status == "applied":
            return {**dict(hit.result), "deduplicated": True}
        prior = hit  # only an applied command is idempotent; a rejected one is evaluated again

    ctx = Ctx(actor, uow, preview=preview)
    try:
        args = spec.args.model_validate(raw)
        await _authorise(ctx, spec, args)
        if preview and not spec.preview:
            return _result(
                cmd, "previewed", preview={"summary": spec.description, "args": raw, "affected": None}
            )
        if spec.destructive and not preview and not (meta.get("confirmed") or meta.get("forced")):
            raise ApiError(409, "confirmation_required", f"{cmd.op} is destructive: preview it, confirm, then send with _meta.confirmed (or --force)", pointer="/args/_meta/confirmed")  # fmt: skip
        uow.context["actor_id"] = actor.person.id
        uow.context["correlation_id"] = correlation_id
        uow.context["command"] = {"op": cmd.op, "client_id": cmd.client_id, "client_version": meta.get("client_version"), "mode": meta.get("mode"), "role_mode": meta.get("role_mode"), **({"forced": True} if meta.get("forced") else {}), **({"on_behalf_of": cmd.on_behalf_of} if cmd.on_behalf_of else {})}  # fmt: skip
        uow.hold_commits = True  # every router function the handler calls flushes; the one commit is below
        out = await spec.handler(ctx, args)
        if preview:
            await _reset(uow, actor)  # a preview never writes, whatever the handler did to compute it
            return _result(cmd, "previewed", preview=out.preview if out.preview is not None else out.result)
        result = _result(cmd, "applied", result=out.result)
        if prior is not None:
            prior.op, prior.status, prior.result = cmd.op, "applied", result
        else:
            uow.session.add(m.CommandLog(id=new_id(), workspace_id=actor.ws.id, client_id=cmd.client_id, actor_id=actor.person.id, op=cmd.op, status="applied", result=result))  # fmt: skip
        emit_admin(
            uow,
            workspace_id=actor.ws.id,
            actor_id=actor.person.id,
            event_type="command.applied",
            payload={"after": {"op": cmd.op, "client_id": cmd.client_id, "status": "applied"}},
        )  # the CommandLog row rides on a real event, so the commit guard needs no allow-list entry
        uow.hold_commits = False
        await uow.commit()
        return result
    except (ValidationError, ApiError, DomainError, IntegrityError, StatementError) as exc:
        await _reset(uow, actor)
        return _result(cmd, "rejected", errors=_summary(exc))
    finally:
        uow.hold_commits = False
        uow.context.pop("command", None)
