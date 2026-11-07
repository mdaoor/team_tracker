"""Gateway authentication: shared secrets from the environment (issued by the deployment), optional anonymous
loopback mode for the laptop sidecar."""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Depends, Header, Request

from tt_llm.config import GatewayConfig
from tt_llm.routing import GatewayError


class Unauthorized(GatewayError):
    status, code = 401, "unauthorized"


class Forbidden(GatewayError):
    status, code = 403, "forbidden"


def _tokens(env_name: str) -> list[str]:
    return [t.strip() for t in os.environ.get(env_name, "").split(",") if t.strip()]


def _matches(presented: str | None, accepted: list[str]) -> bool:
    return presented is not None and any(hmac.compare_digest(presented, a) for a in accepted)


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _workspace_tokens(env_name: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in _tokens(env_name):
        tok, _, ws = pair.partition("=")
        if tok and ws:
            out[tok] = ws
    return out


async def require_caller(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    """Shared and admin tokens may name any workspace (the server runs the whole deployment); a workspace token is bound to one
    workspace: its budget, runs and limits cannot be charged to another by writing a different `workspace` in the body."""
    cfg: GatewayConfig = request.app.state.cfg
    presented = _bearer(authorization)
    accepted = _tokens(cfg.auth.tokens_env) + _tokens(cfg.auth.admin_env)
    if _matches(presented, accepted):
        return
    bound = _workspace_tokens(cfg.auth.workspace_tokens_env)
    for tok, ws in bound.items():
        if presented is not None and hmac.compare_digest(presented, tok):
            if request.method == "POST":
                try:
                    claimed = (await request.json()).get("workspace")
                except ValueError:
                    claimed = None
                if claimed != ws:
                    raise Forbidden(f"this token is bound to workspace {ws!r}")
            return
    if cfg.auth.allow_anonymous and not accepted and not bound:
        return
    raise Unauthorized("a gateway token is required")


async def require_admin(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    cfg: GatewayConfig = request.app.state.cfg
    admin = _tokens(cfg.auth.admin_env)
    if admin and _matches(_bearer(authorization), admin):
        return
    if cfg.auth.allow_anonymous and not admin:
        return
    raise Forbidden("admin token required")


Caller = Depends(require_caller)
Admin = Depends(require_admin)
