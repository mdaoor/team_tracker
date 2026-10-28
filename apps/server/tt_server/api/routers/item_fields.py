"""Custom field definitions and per-item values (plan P1)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from tt_schema.base import new_id, utcnow
from tt_schema.enums import FieldType, TargetType

from tt_server.api.authz import needs
from tt_server.api.deps import Actor, CurrentActor, Uow
from tt_server.api.errors import ApiError
from tt_server.api.overlay_support import audit
from tt_server.api.routers.core import _item, _project
from tt_server.db import models as m
from tt_server.domain.common import row_dict

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["items"])


# ------------------------------------------------------------------ custom fields
KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class FieldIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    key: str = Field(pattern=KEY.pattern)
    field_type: FieldType
    project: str | None = None
    options: list[str] | None = None
    applies_to_types: list[str] | None = None
    is_required: bool = False


def _need_options(t: FieldType, options: list[str] | None) -> None:
    if t in (FieldType.SELECT, FieldType.MULTI_SELECT) and not options:
        raise ApiError(400, "invalid_field", "select fields need `options`", pointer="/options")


@router.post("/custom-fields", status_code=201, dependencies=[needs("workspace.admin")])
async def create_field(body: FieldIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    _need_options(body.field_type, body.options)
    project_id = (await _project(uow, actor.ws.id, body.project)).id if body.project else None
    dup = await uow.session.scalar(
        select(m.CustomFieldDefinition.id).where(
            m.CustomFieldDefinition.workspace_id == actor.ws.id, m.CustomFieldDefinition.key == body.key,
            m.CustomFieldDefinition.project_id.is_(None) if project_id is None else m.CustomFieldDefinition.project_id == project_id,
            m.CustomFieldDefinition.deleted_at.is_(None),
        )
    )  # fmt: skip
    if dup:
        raise ApiError(
            409, "duplicate_field", f"a field with key {body.key!r} already exists here", pointer="/key"
        )
    f = m.CustomFieldDefinition(
        id=new_id(), workspace_id=actor.ws.id, name=body.name, key=body.key, field_type=body.field_type, project_id=project_id,
        options=body.options, applies_to_types=body.applies_to_types, is_required=body.is_required,
    )  # fmt: skip
    uow.session.add(f)
    await uow.session.flush()
    audit(
        uow,
        actor,
        TargetType.PROJECT,
        project_id or actor.ws.id,
        "custom_field.created",
        key=f.key,
        field_type=f.field_type.value,
    )
    await uow.commit()
    return row_dict(f)


@router.get("/custom-fields")
async def list_fields(actor: CurrentActor, uow: Uow, project: str | None = None) -> dict[str, Any]:
    q = select(m.CustomFieldDefinition).where(
        m.CustomFieldDefinition.workspace_id == actor.ws.id, m.CustomFieldDefinition.deleted_at.is_(None)
    )
    if project:
        p = await _project(uow, actor.ws.id, project)
        q = q.where(
            (m.CustomFieldDefinition.project_id.is_(None)) | (m.CustomFieldDefinition.project_id == p.id)
        )
    return {
        "data": [
            row_dict(f) for f in (await uow.session.scalars(q.order_by(m.CustomFieldDefinition.key))).all()
        ],
        "next_cursor": None,
    }


class FieldPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    options: list[str] | None = None
    is_required: bool | None = None


@router.patch("/custom-fields/{fid}", dependencies=[needs("workspace.admin")])
async def patch_field(fid: str, body: FieldPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    f = await _field(uow, actor, fid)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(f, k, v)
    _need_options(f.field_type, f.options)
    audit(uow, actor, TargetType.PROJECT, f.project_id or actor.ws.id, "custom_field.updated", key=f.key)
    await uow.commit()
    return row_dict(f)


@router.delete("/custom-fields/{fid}", status_code=204, dependencies=[needs("workspace.admin")])
async def delete_field(fid: str, actor: CurrentActor, uow: Uow) -> Response:
    f = await _field(uow, actor, fid)
    f.deleted_at = utcnow()
    audit(uow, actor, TargetType.PROJECT, f.project_id or actor.ws.id, "custom_field.deleted", key=f.key)
    await uow.commit()
    return Response(status_code=204)


async def _field(uow: Uow, actor: Actor, fid: str) -> m.CustomFieldDefinition:
    f = await uow.session.scalar(
        select(m.CustomFieldDefinition).where(
            m.CustomFieldDefinition.id == fid, m.CustomFieldDefinition.workspace_id == actor.ws.id, m.CustomFieldDefinition.deleted_at.is_(None)
        )
    )  # fmt: skip
    if f is None:
        raise ApiError(404, "not_found", "custom field not found")
    return f


class ValueIn(BaseModel):
    __module__ = "tt_server.api.routers.item_extras"  # pins the published OpenAPI schema name

    value: Any = None


ValueIn.model_rebuild(_types_namespace={"Any": Any})


def _coerce(f: m.CustomFieldDefinition, v: Any) -> dict[str, Any]:
    """Validate against the definition; returns the typed columns to set."""
    empty: dict[str, Any] = {"value_text": None, "value_number": None, "value_date": None, "value_json": None}
    if v is None:
        return empty
    t = f.field_type
    try:
        if t in (FieldType.TEXT, FieldType.URL, FieldType.PERSON):
            if not isinstance(v, str):
                raise ValueError("expected text")
            if t == FieldType.URL and not re.match(r"^https?://", v):
                raise ValueError("expected an http(s) URL")
            return {**empty, "value_text": v}
        if t == FieldType.NUMBER:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("expected a number")
            return {**empty, "value_number": v}
        if t == FieldType.DATE:
            return {**empty, "value_date": date.fromisoformat(str(v))}
        if t == FieldType.BOOL:
            if not isinstance(v, bool):
                raise ValueError("expected true or false")
            return {**empty, "value_json": v}
        opts = set(f.options or [])
        if t == FieldType.SELECT:
            if v not in opts:
                raise ValueError(f"must be one of {sorted(opts)}")
            return {**empty, "value_text": v}
        if not isinstance(v, list) or not set(v) <= opts:
            raise ValueError(f"expected a list drawn from {sorted(opts)}")
        return {**empty, "value_json": v}
    except ValueError as exc:
        raise ApiError(400, "invalid_value", f"{f.key}: {exc}", pointer="/value") from exc


@router.put("/items/{ref}/custom-fields/{key}", dependencies=[needs("item.write_overlay", item="ref")])
async def set_value(ref: str, key: str, body: ValueIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, ref)
    f = await uow.session.scalar(
        select(m.CustomFieldDefinition)
        .where(
            m.CustomFieldDefinition.workspace_id == actor.ws.id,
            m.CustomFieldDefinition.key == key,
            m.CustomFieldDefinition.deleted_at.is_(None),
            (m.CustomFieldDefinition.project_id.is_(None))
            | (m.CustomFieldDefinition.project_id == item.project_id),
        )
        .order_by(m.CustomFieldDefinition.project_id.is_(None))
    )  # fmt: skip  (a project-level field shadows a workspace-level one)
    if f is None:
        raise ApiError(404, "not_found", f"no custom field {key!r}")
    if f.applies_to_types and item.type_id not in f.applies_to_types:
        raise ApiError(400, "invalid_value", f"{key} does not apply to this item type")
    if body.value is None and f.is_required:
        raise ApiError(400, "invalid_value", f"{key} is required", pointer="/value")
    cols = _coerce(f, body.value)
    row = await uow.session.scalar(
        select(m.CustomFieldValue).where(
            m.CustomFieldValue.work_item_id == item.id, m.CustomFieldValue.field_definition_id == f.id
        )
    )
    if row is None:
        row = m.CustomFieldValue(id=new_id(), work_item_id=item.id, field_definition_id=f.id)
        uow.session.add(row)
    for k, v in cols.items():
        setattr(row, k, v)
    await uow.session.flush()
    audit(uow, actor, TargetType.WORK_ITEM, item.id, "item.custom_field_set", key=key)
    await uow.commit()
    return {"key": key, "value": body.value}


@router.get("/items/{ref}/custom-fields")
async def get_values(ref: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    item = await _item(uow, actor.ws.id, ref)
    rows = (await uow.session.execute(
        select(m.CustomFieldDefinition, m.CustomFieldValue)
        .join(m.CustomFieldValue, m.CustomFieldValue.field_definition_id == m.CustomFieldDefinition.id)
        .where(m.CustomFieldValue.work_item_id == item.id, m.CustomFieldDefinition.deleted_at.is_(None))
    )).all()  # fmt: skip
    out: dict[str, Any] = {}
    for f, v in rows:
        if v.value_text is None and v.value_number is None and v.value_date is None and v.value_json is None:
            continue  # a cleared value
        out[f.key] = (
            v.value_text if v.value_text is not None
            else float(v.value_number) if v.value_number is not None
            else v.value_date.isoformat() if v.value_date is not None
            else v.value_json
        )  # fmt: skip
    return {"data": out}
