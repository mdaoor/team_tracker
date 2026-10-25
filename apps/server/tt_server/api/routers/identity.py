"""Identity-provider settings: group -> role / team mappings and on-demand directory sync (Phase 4 §4.6)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from tt_schema.enums import ScopeType

from tt_server.api.authz import needs
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.domain import idp_mappings as svc
from tt_server.domain.common import row_dict
from tt_server.integrations import idp

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["identity"])


class MappingIn(BaseModel):
    provider: Literal["ldap", "oidc"] = "ldap"
    group: str = Field(min_length=1, description="AD group DN or OIDC group id")
    role: str | None = None
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: str | None = None
    team: str | None = Field(default=None, description="team slug or id to add members to")


@router.get("/idp/mappings", dependencies=[needs("role.admin")])
async def list_mappings(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    return {"data": await svc.list_mappings(uow, actor.ws.id), "next_cursor": None}


@router.post("/idp/mappings", status_code=201, dependencies=[needs("role.admin")])
async def create_mapping(body: MappingIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await svc.create_mapping(
        uow,
        actor.ws.id,
        actor.person.id,
        provider=body.provider,
        group=body.group,
        role=body.role,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        team=body.team,
    )
    await uow.commit()
    return row_dict(row)


@router.delete("/idp/mappings/{mapping_id}", status_code=204, dependencies=[needs("role.admin")])
async def delete_mapping(mapping_id: str, actor: CurrentActor, uow: Uow) -> Response:
    await svc.delete_mapping(uow, actor.ws.id, actor.person.id, mapping_id)
    await uow.commit()
    return Response(status_code=204)


@router.post("/idp/sync", dependencies=[needs("role.admin")])
async def sync_now(request: Request, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Run the directory sync for this workspace now (the nightly job does the same for every workspace)."""
    ldap = request.app.state.ldap
    if ldap is None:
        raise ApiError(409, "not_configured", "LDAP is not configured on this server")
    res = await idp.sync_workspace(uow, actor.ws, ldap.lookup_email)
    return {"checked": res.checked, "changed": res.changed, "missing": res.missing, "log": res.log}
