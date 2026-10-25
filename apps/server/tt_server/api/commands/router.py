"""`POST /commands`, `GET /commands/catalogue` and the direct `POST /items:merge`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from tt_schema.base import new_id, utcnow
from tt_schema.client import ClientCommand

from tt_server.api.commands import ops
from tt_server.api.commands.execute import can_send_commands, run
from tt_server.api.commands.registry import catalogue
from tt_server.api.deps import CurrentActor, Uow
from tt_server.api.errors import ApiError

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["commands"])

MAX_COMMANDS = 50


class CommandsIn(BaseModel):
    workspace: str | None = None
    commands: list[ClientCommand] = Field(min_length=1, max_length=MAX_COMMANDS)


@router.get("/commands/catalogue")
async def get_catalogue(request: Request, actor: CurrentActor) -> Response:
    """Every op with its JSON Schema, permission and safety flags. Conditional on `If-None-Match`."""
    cat = catalogue()
    etag = cat["etag"]
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(cat, headers={"ETag": etag})


async def run_all(
    uow: Uow, actor: CurrentActor, commands: list[ClientCommand], correlation_id: str
) -> list[dict[str, Any]]:
    return [await run(uow, actor, c, correlation_id=correlation_id) for c in commands]


@router.post("/commands")
async def post_commands(body: CommandsIn, actor: CurrentActor, uow: Uow) -> JSONResponse:
    """Apply or preview commands in order; a rejected command never fails the others. 207 when any was rejected."""
    if not can_send_commands(actor):
        raise ApiError(403, "insufficient_scope", "this token may only send capture messages")
    results = await run_all(uow, actor, body.commands, new_id())
    status = 207 if any(r["status"] == "rejected" for r in results) else 200
    return JSONResponse({"results": results}, status)


class MergeIn(BaseModel):
    sources: list[str] = Field(min_length=1)
    target: str | None = None
    new_title: str | None = None
    allow_cross_project: bool = False
    preview: bool = False


@router.post("/items:merge")
async def merge_items(body: MergeIn, actor: CurrentActor, uow: Uow) -> JSONResponse:
    """Direct form of `item.merge` (same service, same permission, same events)."""
    if not can_send_commands(actor):
        raise ApiError(403, "insufficient_scope", "this token may not merge items")
    cmd = ClientCommand(
        client_id=new_id(), actor=actor.handle, at=utcnow(), op="item.merge", preview_only=body.preview,
        args={**body.model_dump(exclude={"preview"}), "_meta": {"confirmed": True, "mode": "direct"}},
    )  # fmt: skip
    res = await run(uow, actor, cmd, correlation_id=new_id())
    if res["status"] == "rejected":
        e = res["errors"][0]
        status = {"not_found": 404, "forbidden": 403}.get(e["code"], 400)
        return JSONResponse({"error": {"code": e["code"], "message": e["message"], "pointer": e.get("pointer"), "details": e.get("details", {})}}, status)  # fmt: skip
    return JSONResponse(res)


_ = ops  # importing registers every op
