"""Summaries from facts (Phase 2b): project / epic / product insights and the daily log."""

from __future__ import annotations

import json
from typing import Any

from tt_llm.guards import KEY_RE, wrap_untrusted
from tt_llm.routing import BadModelOutput
from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.tasks.base import Deps, finish, new_run, respond, system_prompt

MAX_WORDS = 120
SCOPES = {"project", "epic", "product"}
_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string"},
        "supporting_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["body", "supporting_ids"],
}


def known_ids(facts: Any) -> set[str]:
    out: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k, v in x.items():
                if k in {"id", "key", "item_key"} and isinstance(v, str):
                    out.add(v)
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(facts)
    return out


async def summarise(deps: Deps, req: TaskRequest, scope: str) -> TaskResponse:
    if scope not in SCOPES:
        raise BadModelOutput(f"unknown summary scope {scope!r}")
    run = new_run(f"summarise_{scope}", req, req.options.model_alias or "summariser")
    messages = [
        {"role": "system", "content": system_prompt(deps, "summarise")},
        {
            "role": "user",
            "content": f"scope: {scope}\n"
            + wrap_untrusted("facts", json.dumps(req.facts, default=str, ensure_ascii=False)),
        },
    ]
    try:
        data, comp = await deps.router.structured(
            req.options.model_alias or "summariser",
            messages,
            _SCHEMA,
            workspace=req.workspace or "-",
            privacy_mode=req.options.privacy_mode,
        )
    except Exception:
        await finish(deps, run, "error")
        raise
    run.model, run.cost_usd = comp.model, comp.cost_usd
    known = known_ids(req.facts)
    warnings: list[str] = []
    body = str(data["body"]).strip()
    supporting = [i for i in data["supporting_ids"] if i in known]
    if len(supporting) != len(data["supporting_ids"]):
        warnings.append("dropped supporting ids that are not in the facts")
    invented = sorted({k for k in KEY_RE.findall(body) if k not in known})
    if invented:
        warnings.append(f"summary mentions ids absent from the facts: {', '.join(invented)}")
    words = len(body.split())
    if words > MAX_WORDS:
        warnings.append(f"summary is {words} words (limit {MAX_WORDS})")
    risk_mentions = sum(
        1
        for r in req.facts.get("risks", [])
        if isinstance(r, dict) and str(r.get("title", "")).lower() in body.lower()
    )
    if risk_mentions > 1:
        warnings.append("summary mentions more than one risk")
    await finish(deps, run)
    return respond(
        run,
        output={"body": body, "supporting_ids": supporting, "grounded": not invented, "words": words},
        warnings=warnings,
    )


async def summarise_daily_log(deps: Deps, req: TaskRequest) -> TaskResponse:
    run = new_run("summarise_daily_log", req, "summariser")
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
    messages = [
        {"role": "system", "content": system_prompt(deps, "daily_log")},
        {
            "role": "user",
            "content": wrap_untrusted("facts", json.dumps(req.facts, default=str, ensure_ascii=False)),
        },
    ]
    try:
        data, comp = await deps.router.structured(
            "summariser",
            messages,
            schema,
            workspace=req.workspace or "-",
            privacy_mode=req.options.privacy_mode,
        )
    except Exception:
        await finish(deps, run, "error")
        raise
    run.model, run.cost_usd = comp.model, comp.cost_usd
    known = known_ids(req.facts)
    invented = sorted({k for k in KEY_RE.findall(data["summary"]) if k not in known})
    await finish(deps, run)
    return respond(
        run,
        output={"summary": data["summary"].strip(), "grounded": not invented},
        warnings=[f"mentions ids absent from the facts: {', '.join(invented)}"] if invented else [],
    )
