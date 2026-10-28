"""`POST /v1/translate/personal`: classify a line from the personal planner (Phase 15 §15.2). Never sends or stores anything.

The model may refine what the deterministic parser could not decide - `kind`/`category` when no label, keyword or `#tag` said it, and a
`project_hint` that exists in the pack - but only toward *more* privacy by itself; a less private reading is returned as `suggested_*` for the person to confirm. Times, durations, dates and RRULEs come from `tt_client.parser.personal`, always (ADR-0003).
A personal line is private by construction, so the request is pinned to `privacy_mode=local_only`: a cloud model is never used for it.
"""

from __future__ import annotations

import json
from typing import Any

from tt_client.parser.personal import parse_personal
from tt_client.parser.personal_llm import refine
from tt_client.parser.structured import ParseContext
from tt_client.personal.model import CATEGORY_DEFAULT_SHARE, Category, Kind

from tt_llm.guards import wrap_untrusted
from tt_llm.routing import BadModelOutput
from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.tasks.base import Deps, finish, new_run, respond, system_prompt

PERSONAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "drafts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": [k.value for k in Kind]},
                    "category": {"type": "string", "enum": [c.value for c in Category]},
                    "project_hint": {"type": ["string", "null"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "kind", "category"],
            },
        }
    },
    "required": ["drafts"],
}


def merge(
    parsed: list[Any], model: list[dict[str, Any]], project_keys: set[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Parser drafts, refined by the model through the same `refine` the client uses (more private: applied; less private: a suggestion)."""
    notes: list[str] = []
    out: list[dict[str, Any]] = []
    for i, d in enumerate(parsed):
        m = model[i] if i < len(model) else None
        draft = d
        if m:
            draft, more = refine(
                d,
                _enum(Kind, m.get("kind")),
                _enum(Category, m.get("category")),
                m.get("project_hint"),
                project_keys,
            )
            notes += more
        out.append(
            {**draft.model_dump(mode="json"), "share_policy": CATEGORY_DEFAULT_SHARE[draft.category].value}
        )
    return out, notes


def _enum(cls: Any, value: Any) -> Any:
    try:
        return cls(str(value))
    except ValueError:
        return None


async def translate_personal(deps: Deps, req: TaskRequest) -> TaskResponse:
    lines = req.all_lines()
    if not lines:
        raise BadModelOutput("no line to classify")
    line = lines[0]
    ctx = ParseContext(actor=req.actor, now=req.now, tz=req.time_zone)
    parsed = parse_personal(line, ctx)
    keys = {str(p["key"]).upper() for p in req.pack.get("projects", [])}
    compact = {
        "projects": [{"key": p["key"], "name": p.get("name", "")} for p in req.pack.get("projects", [])]
    }
    run = new_run("translate_personal", req, req.options.model_alias or "translator")
    notes: list[str] = []
    try:
        system = system_prompt(deps, "translate_personal", pack=json.dumps(compact, ensure_ascii=False))
        user = (
            wrap_untrusted("reporter_text", line)
            + f"\nnow: {req.now.isoformat()}\ntime_zone: {req.time_zone}"
        )
        data, comp = await deps.router.structured(
            req.options.model_alias or "translator",
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            PERSONAL_SCHEMA,
            workspace=req.workspace or "-",
            timeout=deps.cfg.limits.translate_timeout_s,
            privacy_mode="local_only",
        )
        run.model, run.cost_usd = comp.model, comp.cost_usd
        model_drafts = (
            [d for d in data.get("drafts", []) if isinstance(d, dict)] if isinstance(data, dict) else []
        )
        drafts, notes = merge(parsed, model_drafts, keys)
        await finish(deps, run)
    except Exception:
        await finish(deps, run, "error")
        raise
    if req.options.send:
        notes.append("personal lines are never sent; options.send was ignored")
    return respond(run, output={"drafts": drafts}, warnings=notes)
