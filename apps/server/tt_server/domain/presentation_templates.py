"""Presentation templates as data (plan P7 §7.5): the JSON schema, validation with JSON pointers, instantiation with parameters."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tt_schema.base import new_id

from tt_server.db import models as m
from tt_server.domain import presentations as pres
from tt_server.domain.errors import NotFound
from tt_server.domain.presentation_template_body import _OPS as _OPS
from tt_server.domain.presentation_template_body import ALL_SCOPES as ALL_SCOPES
from tt_server.domain.presentation_template_body import PLACEHOLDER as PLACEHOLDER
from tt_server.domain.presentation_template_body import SCHEMA_VERSION as SCHEMA_VERSION
from tt_server.domain.presentation_template_body import VARIABLES as VARIABLES
from tt_server.domain.presentation_template_body import WHEN as WHEN
from tt_server.domain.presentation_template_body import Param as Param
from tt_server.domain.presentation_template_body import PostRule as PostRule
from tt_server.domain.presentation_template_body import ScopeKind as ScopeKind
from tt_server.domain.presentation_template_body import TemplateBody as TemplateBody
from tt_server.domain.presentation_template_body import Theme as Theme
from tt_server.domain.presentation_template_body import TSlide as TSlide
from tt_server.domain.presentation_template_body import _loosen as _loosen
from tt_server.domain.presentation_template_body import _missing as _missing
from tt_server.domain.presentation_template_body import _pointer as _pointer
from tt_server.domain.presentation_template_body import _strings as _strings
from tt_server.domain.presentation_template_body import _substitute as _substitute
from tt_server.domain.presentation_template_body import instantiate as instantiate
from tt_server.domain.presentation_template_body import json_schema as json_schema
from tt_server.domain.presentation_template_body import resolve_params as resolve_params
from tt_server.domain.presentation_template_body import validate_body as validate_body

TEMPLATE_DIR = (
    Path(os.environ.get("TT_TEMPLATE_DIR", ""))
    if os.environ.get("TT_TEMPLATE_DIR")
    else Path(__file__).resolve().parents[4] / "apps" / "presentation" / "templates"
)


# ---------------------------------------------------------------- storage
def builtin_bodies() -> list[dict[str, Any]]:
    """Load the template JSON files shipped with the server."""
    return [json.loads(p.read_text()) for p in sorted(TEMPLATE_DIR.glob("*.json"))]


async def ensure_builtins(s: AsyncSession) -> int:
    """Idempotent upsert of the shipped templates by (key, version): unchanged rows are left alone; a changed body under the same
    version is refused loudly (bump `version` in the file)."""
    n = 0
    existing = {
        (r.key, r.version): r
        for r in (
            await s.scalars(
                select(m.PresentationTemplate).where(m.PresentationTemplate.workspace_id.is_(None))
            )
        ).all()
    }
    for raw in builtin_bodies():
        body, errors = validate_body(raw)
        if body is None:
            raise RuntimeError(f"built-in template {raw.get('key')!r} is invalid: {errors[:3]}")
        version = int(raw.get("version", 1))
        row = existing.get((body.key, version))
        dumped = body.model_dump(mode="json")
        if row is None:
            s.add(
                m.PresentationTemplate(
                    id=new_id(),
                    workspace_id=None,
                    key=body.key,
                    version=version,
                    is_builtin=True,
                    is_shared=True,
                    body=dumped,
                )
            )
            n += 1
        elif row.body != dumped:
            row.body = dumped  # same version, edited file: keep the table in step with the shipped file
            n += 1
    if n:
        await s.flush()
    return n


async def find(
    s: AsyncSession, ws_id: str, ref: str, *, viewer_id: str | None = None
) -> m.PresentationTemplate:
    """By id or key: the workspace's own first, then shared ones, then built-ins (latest version)."""
    rows = (
        await s.scalars(
            select(m.PresentationTemplate)
            .where(
                (m.PresentationTemplate.id == ref) | (m.PresentationTemplate.key == ref),
                (m.PresentationTemplate.workspace_id == ws_id)
                | m.PresentationTemplate.workspace_id.is_(None),
                m.PresentationTemplate.deleted_at.is_(None),
            )
            .order_by(m.PresentationTemplate.workspace_id.is_(None), m.PresentationTemplate.version.desc())
        )
    ).all()
    for r in rows:
        if r.workspace_id is None or r.is_shared or r.owner_id == viewer_id or viewer_id is None:
            return r
    raise NotFound("not_found", f"unknown presentation template {ref!r}")


def view(r: m.PresentationTemplate) -> dict[str, Any]:
    """Serialise a stored template for the API."""
    b = r.body
    return {
        "id": r.id, "key": r.key, "version": r.version, "name": b["name"], "description": b.get("description", ""), "is_builtin": r.is_builtin,
        "is_shared": r.is_shared, "owner_id": r.owner_id, "workspace_id": r.workspace_id, "scope_types": b.get("scope_types"),
        "params": b.get("params", []), "slides": [sl["kind"] for sl in b["slides"]], "body": b,
    }  # fmt: skip


def slide_kinds() -> list[dict[str, Any]]:
    """The catalogue templates and external LLMs read instead of the code: config schema, defaults, requires/render_empty."""
    out = []
    for kind in pres.SLIDE_KINDS:
        required, defaults = pres.CONFIG[kind]
        props: dict[str, Any] = {}
        for k in (*required, *defaults):
            d = defaults.get(k)
            t: dict[str, Any] = (
                {"type": "boolean"}
                if isinstance(d, bool)
                else {"type": "integer"}
                if isinstance(d, int)
                else {"type": "array"}
                if isinstance(d, list)
                else {"type": "object"}
                if isinstance(d, dict)
                else {"type": ["string", "null"]}
            )
            if k in ("dimA", "dimB"):
                t = {"type": ["string", "null"], "enum": [*sorted(pres.EXPLORER_DIMS), None]}
            if k == "measure" and kind == "explorer":
                t = {"type": "string", "enum": sorted(pres.EXPLORER_MEASURES)}
            if k == "rows" and kind == "gantt":
                t = {"type": "string", "enum": ["epic", "item", "milestone"]}
            props[k] = {**t, **({"default": d} if k in defaults else {})}
        out.append(
            {
                "kind": kind, **pres.KIND_META[kind],
                "config_schema": {"type": "object", "properties": props, "required": list(required), "additionalProperties": True},
            }
        )  # fmt: skip
    return out
