"""Suggestions the server stores as defaults (review queue, classification pre-fill)."""

from __future__ import annotations

import json
from typing import Any

from tt_llm.guards import wrap_untrusted
from tt_llm.routing import BadModelOutput
from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.tasks.base import Deps, finish, new_run, respond, system_prompt

CHOICE_FIELD = {
    "unclassified_project": ("project", "projects"),
    "unknown_person": ("handle", "people"),
    "stage_suggestion": ("stage", "stages"),
    "deferred_stage": ("stage", "stages"),
    "product_suggestion": ("product", "products"),
    "deferred_product": ("product", "products"),
}
_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "choice": {"type": "object"},
                    "confidence": {"type": "number"},
                    "why": {"type": "string"},
                },
                "required": ["choice", "confidence"],
            },
        }
    },
    "required": ["suggestions"],
}
_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "dimension": {"type": "string"},
                    "value": {"type": ["string", "null"]},
                    "confidence": {"type": "number"},
                },
                "required": ["key", "dimension", "value"],
            },
        }
    },
    "required": ["items"],
}


async def suggest_review(deps: Deps, req: TaskRequest) -> TaskResponse:
    kind = str(req.row.get("kind", ""))
    if kind not in CHOICE_FIELD:
        raise BadModelOutput(f"no suggestion strategy for review kind {kind!r}")
    field_name, cand_key = CHOICE_FIELD[kind]
    allowed = {
        str(c).upper() if field_name in {"project", "product"} else str(c)
        for c in req.candidates.get(cand_key, [])
    }
    run = new_run("suggest_review", req, "agent")
    messages = [
        {"role": "system", "content": system_prompt(deps, "suggest_review")},
        {
            "role": "user",
            "content": wrap_untrusted("reporter_text", json.dumps(req.row, default=str, ensure_ascii=False))
            + "\ncandidates: "
            + json.dumps(req.candidates, ensure_ascii=False),
        },
    ]
    try:
        data, comp = await deps.router.structured(
            "agent",
            messages,
            _REVIEW_SCHEMA,
            workspace=req.workspace or "-",
            privacy_mode=req.options.privacy_mode,
        )
    except Exception:
        await finish(deps, run, "error")
        raise
    run.model, run.cost_usd = comp.model, comp.cost_usd
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for s in data["suggestions"]:
        raw = s["choice"].get(field_name)
        norm = str(raw).upper() if field_name in {"project", "product"} else str(raw)
        if norm not in allowed:
            warnings.append(f"dropped suggestion {raw!r}: not one of the candidates")
            continue
        out.append(
            {
                "choice": {field_name: norm},
                "confidence": max(0.0, min(1.0, float(s["confidence"]))),
                "why": s.get("why", ""),
            }
        )
    out.sort(key=lambda s: -s["confidence"])
    await finish(deps, run)
    return respond(
        run,
        output={"suggestions": out[:3]},
        confidence=out[0]["confidence"] if out else None,
        warnings=warnings,
    )


async def suggest_classify(deps: Deps, req: TaskRequest) -> TaskResponse:
    values = {
        d["key"]: {v if isinstance(v, str) else v["key"] for v in d.get("values", [])} for d in req.dimensions
    }
    keys = {i["key"] for i in req.items}
    run = new_run("suggest_classify", req, "agent")
    messages = [
        {"role": "system", "content": system_prompt(deps, "suggest_classify")},
        {
            "role": "user",
            "content": wrap_untrusted("reporter_text", json.dumps(req.items, default=str, ensure_ascii=False))
            + "\ndimensions: "
            + json.dumps(req.dimensions, ensure_ascii=False),
        },
    ]
    try:
        data, comp = await deps.router.structured(
            "agent",
            messages,
            _CLASSIFY_SCHEMA,
            workspace=req.workspace or "-",
            privacy_mode=req.options.privacy_mode,
        )
    except Exception:
        await finish(deps, run, "error")
        raise
    run.model, run.cost_usd = comp.model, comp.cost_usd
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for s in data["items"]:
        if s["key"] not in keys or s["dimension"] not in values:
            warnings.append(f"dropped suggestion for unknown {s['key']}/{s['dimension']}")
            continue
        if s["value"] is None:
            continue
        if s["value"] not in values[s["dimension"]]:
            warnings.append(f"dropped value {s['value']!r}: not in dimension {s['dimension']}")
            continue
        out.append({**s, "confidence": max(0.0, min(1.0, float(s.get("confidence", 0))))})
    await finish(deps, run)
    return respond(run, output={"items": out}, warnings=warnings)
