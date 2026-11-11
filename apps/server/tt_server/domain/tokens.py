"""API token primitives: the secret format, its stored hash, and minting a token for a member."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from tt_schema.base import new_id, utcnow
from tt_schema.enums import EventSource, TargetType

from tt_server.acl.permissions import TOKEN_SCOPES
from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit
from tt_server.domain.errors import Forbidden, Invalid

TOKEN_PREFIX = "tt_"  # noqa: S105


def hash_token(token: str) -> str:
    """SHA-256 hex digest under which a token secret is stored; the secret itself is never persisted."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    """A fresh random token secret with the recognisable `tt_` prefix."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def mint_api_token(
    uow: UnitOfWork, member: m.WorkspaceMember, name: str, scopes: list[str], days: int
) -> dict[str, Any]:
    """Create a token for the member (valid `days`), emit `token.created` and return the one-time secret."""
    secret = new_token()
    row = m.ApiToken(
        id=new_id(),
        workspace_id=member.workspace_id,
        person_id=member.person_id,
        name=name,
        hash=hash_token(secret),
        scopes=scopes,
        expires_at=utcnow() + timedelta(days=days),
    )
    uow.session.add(row)
    emit(
        uow,
        workspace_id=member.workspace_id,
        actor_id=member.person_id,
        entity_type=TargetType.PERSON,
        entity_id=member.person_id,
        event_type="token.created",
        payload={"after": {"token_id": row.id, "name": name}},
        source=EventSource.OVERLAY,
    )
    return {"id": row.id, "token": secret, "expires_at": row.expires_at, "scopes": scopes}


def mint_member_token(
    uow: UnitOfWork,
    member: m.WorkspaceMember,
    *,
    name: str,
    scopes: list[str],
    expires_in_days: int | None,
    presenting_scopes: list[str] | None,
) -> dict[str, Any]:
    """Mint a personal token for the member after checking the scopes.

    `scopes` must be a non-empty subset of `TOKEN_SCOPES`. A caller authenticating with a token (`presenting_scopes`, not a
    session) can only mint tokens no stronger than itself, since the scopes are ordered. `expires_in_days=None` never expires."""
    if not scopes or any(x not in TOKEN_SCOPES for x in scopes):
        raise Invalid(
            "invalid_scope", f"scopes must be a non-empty subset of {list(TOKEN_SCOPES)}", pointer="/scopes"
        )
    if presenting_scopes is not None:
        ceiling = max((TOKEN_SCOPES.index(x) for x in presenting_scopes if x in TOKEN_SCOPES), default=-1)
        if any(TOKEN_SCOPES.index(x) > ceiling for x in scopes):
            raise Forbidden(
                "scope_ceiling",
                "a token cannot create a token with a wider scope than its own",
                pointer="/scopes",
            )
    secret = new_token()
    row = m.ApiToken(
        id=new_id(),
        workspace_id=member.workspace_id,
        person_id=member.person_id,
        name=name,
        hash=hash_token(secret),
        scopes=scopes,
        expires_at=utcnow() + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    uow.session.add(row)
    emit(
        uow,
        workspace_id=member.workspace_id,
        actor_id=member.person_id,
        entity_type=TargetType.PERSON,
        entity_id=member.person_id,
        event_type="token.created",
        payload={"after": {"token_id": row.id, "name": name}},
        source=EventSource.OVERLAY,
    )
    return {"id": row.id, "token": secret, "expires_at": row.expires_at, "scopes": scopes}


def mint_service_token(
    uow: UnitOfWork, ws_id: str, person_id: str, *, name: str, expires_in_days: int
) -> dict[str, Any]:
    """A `capture`+`read` token named `service:<name>` for the LLM gateway, when the core server initiates the work."""
    secret = new_token()
    scopes = [s for s in TOKEN_SCOPES if s in {"capture", "read"}]
    row = m.ApiToken(
        id=new_id(),
        workspace_id=ws_id,
        person_id=person_id,
        name=f"service:{name}",
        hash=hash_token(secret),
        scopes=scopes,
        expires_at=utcnow() + timedelta(days=expires_in_days),
    )
    uow.session.add(row)
    emit(
        uow,
        workspace_id=ws_id,
        actor_id=person_id,
        entity_type=TargetType.PERSON,
        entity_id=person_id,
        event_type="token.created",
        payload={"after": {"token_id": row.id, "name": row.name, "service": True}},
    )
    return {"id": row.id, "token": secret, "scopes": scopes, "expires_at": row.expires_at}
