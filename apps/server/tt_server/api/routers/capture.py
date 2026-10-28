"""POST /capture/batches and the daily log endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from tt_schema.base import new_id
from tt_schema.client import ClientBatch

from tt_server.api.authz import check, needs
from tt_server.api.commands.execute import can_send_commands, run
from tt_server.api.deps import CurrentActor, Uow, get_session_factory
from tt_server.api.errors import ApiError, error_body
from tt_server.domain.capture import dailylog
from tt_server.domain.capture.batch_errors import describe_violation
from tt_server.domain.capture.ingest import ingest_batch

router = APIRouter(prefix="/api/v1/w/{ws}")


@router.post("/capture/batches", tags=["capture"], dependencies=[needs("capture.write")])
async def post_batch(request: Request, actor: CurrentActor, uow: Uow) -> JSONResponse:
    """Apply a ClientBatch: messages first, then commands, in order and under one correlation id.

    200 applied/deduplicated, 207 if any message failed or any command was rejected, 422 on schema violation, 403 when the token
    may only capture but the batch carries commands."""
    try:
        raw = await request.json()
    except ValueError:
        return JSONResponse(error_body("malformed_json", "request body is not valid JSON"), 422)
    try:
        batch = ClientBatch.model_validate(raw)
    except ValidationError as exc:
        message, pointer, details = describe_violation(exc)
        return JSONResponse(error_body("schema_violation", message, pointer, details), 422)
    if batch.workspace not in (actor.ws.slug, actor.ws.id):
        return JSONResponse(
            error_body("workspace_mismatch", "batch.workspace does not match the URL", "/workspace"), 422
        )
    if batch.commands and not can_send_commands(actor):
        raise ApiError(403, "insufficient_scope", "this token may only send capture messages, not commands")
    if batch.commands and (limited := request.app.state.throttle_commands(request, len(batch.commands))):
        return limited  # type: ignore[no-any-return]
    corr = new_id()
    results = await ingest_batch(
        get_session_factory(request),
        actor.ws.id,
        batch,
        batch_id=corr,
        agent_run_id=request.headers.get("X-Agent-Run-Id"),
    )
    body: dict[str, Any] = {"results": [r.as_dict() for r in results]}
    command_results = [await run(uow, actor, c, correlation_id=corr) for c in batch.commands]
    if batch.commands:
        body["results"] = [
            *body["results"],
            *command_results,
        ]  # one list, matched by client_id (messages first, then commands)
    failed = any(r.status == "failed" for r in results) or any(
        r["status"] == "rejected" for r in command_results
    )
    return JSONResponse(body, 207 if failed else 200)


@router.get("/daily-logs", tags=["daily-logs"])
async def list_daily_logs(
    actor: CurrentActor, uow: Uow, day: date | None = None, person: str | None = None
) -> dict[str, Any]:
    rows, drafted = await dailylog.list_visible(
        uow, actor.ws, actor.member, actor.acl, actor.person.id, person=person, day=day
    )
    if drafted:
        await uow.commit()
    return {"data": [dailylog.to_dict(r) for r in rows], "next_cursor": None}


@router.post("/daily-logs/{log_id}/confirm", tags=["daily-logs"])
async def confirm(log_id: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await dailylog.get_for(uow, actor.ws.id, log_id)
    if row.person_id != actor.person.id:
        raise ApiError(404, "not_found", "daily log not found")
    await dailylog.confirm(uow, actor.ws, row, actor.person.id)
    await uow.commit()
    return dailylog.to_dict(row)


@router.post("/daily-logs/{log_id}/review", tags=["daily-logs"])
async def review(log_id: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    row = await dailylog.get_for(uow, actor.ws.id, log_id)
    check(actor, "dailylog.review")
    if not (actor.acl.is_admin or actor.acl.is_line_manager_of(row.person_id)):
        raise ApiError(403, "forbidden", "only the subject's line manager may review a daily log")
    await dailylog.review(uow, actor.ws, row, actor.person.id)
    await uow.commit()
    return dailylog.to_dict(row)
