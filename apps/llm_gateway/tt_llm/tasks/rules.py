"""`POST /v1/rules/dry-run` -- which sample lines would parse differently under a candidate rule set."""

from __future__ import annotations

import asyncio
from typing import Any

from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.tasks.base import Deps, finish, new_run, respond
from tt_llm.tasks.translate import translate_line

MAX_LINES = 20
IGNORED = {"client_id", "at", "raw_input", "actor"}


def _fields(msg: Any) -> dict[str, Any]:
    return {
        k: v
        for k, v in msg.model_dump(mode="json", exclude_none=True).items()
        if k not in IGNORED and v not in ([], {})
    }


async def rules_dry_run(deps: Deps, req: TaskRequest) -> TaskResponse:
    run = new_run("rules_dry_run", req, "translator")
    current = list((req.pack or {}).get("manager_rules", []))
    lines = req.all_lines()[:MAX_LINES]

    async def one(line: str) -> dict[str, Any]:
        (b0, _, c0), (b1, _, c1) = await asyncio.gather(
            translate_line(deps, req, line, rules=current), translate_line(deps, req, line, rules=req.rules)
        )
        run.cost_usd += c0.cost_usd + c1.cost_usd
        run.model = c1.model
        before = [_fields(m) for m in b0.messages]
        after = [_fields(m) for m in b1.messages]
        changed = sorted(
            {k for x, y in zip(before, after, strict=False) for k in {*x, *y} if x.get(k) != y.get(k)}
            | ({"messages"} if len(before) != len(after) else set())
        )
        return {"line": line, "changed": bool(changed), "fields": changed, "before": before, "after": after}

    results = await asyncio.gather(*(one(ln) for ln in lines))
    await finish(deps, run)
    changed = [r for r in results if r["changed"]]
    return respond(run, output={"checked": len(results), "changed": len(changed), "results": changed})
