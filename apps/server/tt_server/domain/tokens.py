"""API token primitives: the secret format, its stored hash, and minting a token for a member."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from tt_schema.base import new_id, utcnow
from tt_schema.enums import EventSource, TargetType

from tt_server.db import models as m
from tt_server.db.uow import UnitOfWork
from tt_server.domain.common import emit

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
