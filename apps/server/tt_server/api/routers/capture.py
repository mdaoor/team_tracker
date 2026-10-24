"""POST /capture/batches and the daily log endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from tt_schema.client import ClientBatch

from tt_server.acl.visibility import visible
from tt_server.api.authz import check, needs
from tt_server.api.deps import CurrentActor, Uow, get_session_factory
from tt_server.api.errors import ApiError, error_body
from tt_server.db import models as m
from tt_server.domain.capture import dailylog
from tt_server.domain.capture.ingest import ingest_batch

router = APIRouter(prefix="/api/v1/w/{ws}")


@router.post("/capture/batches", tags=["capture"], dependencies=[needs("capture.write")])
async def post_batch(request: Request, actor: CurrentActor) -> JSONResponse:
    """Apply a ClientBatch: 200 applied/deduplicated, 207 if any message failed, 422 on schema violation."""
    try:
        raw = await request.json()
    except ValueError:
        return JSONResponse(error_body("malformed_json", "request body is not valid JSON"), 422)
    try:
        batch = ClientBatch.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = [str(p) for p in first["loc"]]
        return JSONResponse(
            error_body(
                "schema_violation",
                first["msg"],
                "/" + "/".join(loc),
                {
                    "message_index": first["loc"][1]
                    if len(first["loc"]) > 1 and first["loc"][0] == "messages"
                    else None
                },
            ),
            422,
        )
    if batch.workspace not in (actor.ws.slug, actor.ws.id):
        return JSONResponse(
            error_body("workspace_mismatch", "batch.workspace does not match the URL", "/workspace"), 422
        )
    results = await ingest_batch(
        get_session_factory(request),
        actor.ws.id,
        batch,
        agent_run_id=request.headers.get("X-Agent-Run-Id"),
    )
    status = 207 if any(r.status == "failed" for r in results) else 200
    return JSONResponse({"results": [r.as_dict() for r in results]}, status)


@router.get("/daily-logs", tags=["daily-logs"])
async def list_daily_logs(
    actor: CurrentActor, uow: Uow, day: date | None = None, person: str | None = None
) -> dict[str, Any]:
    stmt = visible(select(m.DailyLog).where(m.DailyLog.workspace_id == actor.ws.id), actor.acl, m.DailyLog)
    subject = actor.person.id
    if person not in (None, "me"):
        subject = (
            await uow.session.scalar(
                select(m.WorkspaceMember.person_id).where(
                    m.WorkspaceMember.workspace_id == actor.ws.id,
                    (m.WorkspaceMember.handle == person) | (m.WorkspaceMember.person_id == person),
                )
            )
            or ""
        )
    stmt = stmt.where(m.DailyLog.person_id == subject)
    if day:
        stmt = stmt.where(m.DailyLog.date == day)
    rows = (await uow.session.scalars(stmt.order_by(m.DailyLog.date.desc()).limit(100))).all()
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
