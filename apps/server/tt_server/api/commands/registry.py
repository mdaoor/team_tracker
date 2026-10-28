"""The command registry (Phase 14): every management op the client may send, with its schema, permission and safety flags.

A command is a thin, schema-described façade over the same code the web console calls, so authorisation and events are
identical whichever surface is used. The catalogue served to clients (and to the gateway translator) is generated from here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from tt_server.api.deps import Actor, Uow


@dataclass
class Ctx:
    actor: Actor
    uow: Uow
    preview: bool = False


@dataclass
class Outcome:
    result: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None


Handler = Callable[[Ctx, Any], Awaitable[Outcome]]


@dataclass(frozen=True)
class Command:
    op: str
    group: str
    description: str
    args: type[BaseModel]
    handler: Handler
    permission: str | None  # None = any workspace member (their own data)
    scope: str = "workspace"  # workspace | item | project  (what `scope_arg` names)
    scope_arg: str | None = None
    preview: bool = False  # the op can describe its effect without writing
    destructive: bool = False
    modes: tuple[str, ...] = ("manager", "pm", "admin")  # role modes that surface it first
    example_args: dict[str, Any] = field(default_factory=dict)
    example_text: str = ""
    token_scope: str = "manage"  # the lowest API-token scope that may send it (manage | admin)

    def entry(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "group": self.group,
            "description": self.description,
            "permission": self.permission,
            "token_scope": self.token_scope,
            "preview": self.preview,
            "destructive": self.destructive,
            "modes": list(self.modes),
            "args_schema": self.args.model_json_schema(),
            "example": {"args": self.example_args, "text": self.example_text},
        }


REGISTRY: dict[str, Command] = {}


def command(
    op: str,
    group: str,
    description: str,
    args: type[BaseModel],
    *,
    permission: str | None,
    scope: str = "workspace",
    scope_arg: str | None = None,
    preview: bool = False,
    destructive: bool = False,
    modes: tuple[str, ...] = ("manager", "pm", "admin"),
    example_args: dict[str, Any] | None = None,
    example_text: str = "",
    token_scope: str = "manage",
) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        if op in REGISTRY:
            raise RuntimeError(f"duplicate command {op!r}")
        if token_scope not in ("manage", "admin"):
            raise RuntimeError(f"{op!r}: token_scope must be manage or admin, not {token_scope!r}")
        REGISTRY[op] = Command(
            op, group, description, args, fn, permission, scope, scope_arg, preview, destructive, modes,
            example_args or {}, example_text, token_scope,
        )  # fmt: skip
        return fn

    return deco


def catalogue() -> dict[str, Any]:
    ops = [REGISTRY[k].entry() for k in sorted(REGISTRY)]
    etag = '"' + hashlib.sha256(json.dumps(ops, sort_keys=True, default=str).encode()).hexdigest()[:16] + '"'
    return {"etag": etag, "ops": ops}
