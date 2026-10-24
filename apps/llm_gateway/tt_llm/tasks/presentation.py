"""Author tasks (Phase 2b / P7 §7.6): a brief -> a draft deck or a draft template.

The gateway only drafts. It checks slide kinds against the catalogue it was handed and drops what it cannot vouch for; the core server
re-validates everything on import, so a model can never write a deck the product would not accept from a person."""

from __future__ import annotations

import json
from typing import Any

from tt_llm.guards import wrap_untrusted
from tt_llm.routing import BadModelOutput
from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.tasks.base import Deps, finish, new_run, respond, system_prompt

MAX_SLIDES = 20
_DECK = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "title": {"type": ["string", "null"]},
                    "notes": {"type": ["string", "null"]},
                    "config": {"type": "object"},
                },
                "required": ["kind"],
            },
        },
    },
    "required": ["title", "slides"],
}
_TEMPLATE = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "scope_types": {"type": "array", "items": {"type": "string"}},
        "params": {"type": "array", "items": {"type": "object"}},
        "slides": {"type": "array", "items": {"type": "object"}},
        "post_rules": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["key", "name", "slides"],
}


def _kinds(req: TaskRequest) -> set[str]:
    return {k["kind"] for k in req.pack.get("slide_kinds", []) if isinstance(k, dict) and "kind" in k}


def _project_keys(facts: Any) -> set[str]:
    return {p["key"] for p in (facts or {}).get("projects", []) if isinstance(p, dict) and "key" in p}


async def _ask(
    deps: Deps, req: TaskRequest, task: str, schema: dict[str, Any]
) -> tuple[dict[str, Any], Any, Any]:
    if not (req.request or "").strip():
        raise BadModelOutput("request text is required")
    run = new_run(task, req, req.options.model_alias or "reasoner")
    messages = [
        {"role": "system", "content": system_prompt(deps, task)},
        {
            "role": "user",
            "content": wrap_untrusted("request", req.request or "")
            + wrap_untrusted("slide_kinds", json.dumps(req.pack.get("slide_kinds", []), ensure_ascii=False))
            + wrap_untrusted("facts", json.dumps(req.facts, default=str, ensure_ascii=False)),
        },
    ]
    try:
        data, comp = await deps.router.structured(
            req.options.model_alias or "reasoner",
            messages,
            schema,
            workspace=req.workspace or "-",
            privacy_mode=req.options.privacy_mode,
        )
    except Exception:
        await finish(deps, run, "error")
        raise
    run.model, run.cost_usd = comp.model, comp.cost_usd
    return data, run, comp


async def presentation_author(deps: Deps, req: TaskRequest) -> TaskResponse:
    data, run, _ = await _ask(deps, req, "presentation_author", _DECK)
    allowed, projects = _kinds(req), _project_keys(req.facts)
    warnings: list[str] = []
    slides: list[dict[str, Any]] = []
    for s in data["slides"]:
        if allowed and s["kind"] not in allowed:
            warnings.append(f"dropped slide of unknown kind {s['kind']!r}")
            continue
        key = (s.get("config") or {}).get("project_key")
        if projects and isinstance(key, str) and not key.startswith("${") and key not in projects:
            warnings.append(f"dropped {s['kind']} slide for unknown project {key!r}")
            continue
        slides.append({k: v for k, v in s.items() if v is not None})
    if len(slides) > MAX_SLIDES:
        warnings.append(f"truncated to {MAX_SLIDES} slides")
        slides = slides[:MAX_SLIDES]
    await finish(deps, run)
    return respond(run, output={"title": str(data["title"]).strip(), "slides": slides}, warnings=warnings)


async def template_author(deps: Deps, req: TaskRequest) -> TaskResponse:
    data, run, _ = await _ask(deps, req, "template_author", _TEMPLATE)
    allowed = _kinds(req)
    warnings: list[str] = []
    if allowed:
        keep = [s for s in data["slides"] if isinstance(s, dict) and s.get("kind") in allowed]
        if len(keep) != len(data["slides"]):
            warnings.append("dropped template slides of unknown kind")
        data["slides"] = keep
    data.setdefault("schema_version", 1)
    await finish(deps, run)
    return respond(run, output=data, warnings=warnings)
