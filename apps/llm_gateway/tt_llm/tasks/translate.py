"""`POST /v1/translate`: one line + ContextPack -> ClientBatch (Mode A, non-agentic, < 1.5 s)."""

from __future__ import annotations

import json
from typing import Any

from tt_client.pack import ContextPack
from tt_client.parser.llm import postprocess
from tt_client.parser.structured import ParseContext, StructuredParser
from tt_schema.client import ClientBatch

from tt_llm.guards import (
    Grounding,
    enforce,
    fill_from_parser,
    numbers_from_parser,
    typed_handles,
    typed_keys,
    wrap_untrusted,
)
from tt_llm.routing import BadModelOutput
from tt_llm.schemas import TaskRequest, TaskResponse, fill_identity, model_batch_schema
from tt_llm.tasks.base import Deps, finish, new_run, respond, system_prompt


def mean_confidence(batch: ClientBatch) -> float | None:
    vals = [v for m in batch.messages for v in (m.llm_confidence or {}).values()]
    return round(sum(vals) / len(vals), 3) if vals else None


async def translate_line(
    deps: Deps, req: TaskRequest, line: str, *, rules: list[str] | None = None
) -> tuple[ClientBatch, list[str], Any]:
    """Shared by `/v1/translate` and the rules dry-run. Returns (batch, notes, completion)."""
    pack = dict(req.pack)
    compact = ContextPack(pack).compact() if pack else {}
    if rules is not None:
        compact["manager_rules"] = rules
    system = system_prompt(deps, "translate", pack=json.dumps(compact, ensure_ascii=False))
    user = (
        wrap_untrusted("reporter_text", line)
        + f"\nactor: {req.actor}\nnow: {req.now.isoformat()}\ntime_zone: {req.time_zone}"
    )
    data, comp = await deps.router.structured(
        req.options.model_alias or "translator",
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        model_batch_schema(),
        workspace=req.workspace or "-",
        timeout=deps.cfg.limits.translate_timeout_s,
        privacy_mode=req.options.privacy_mode,
    )
    try:
        batch = ClientBatch.model_validate(
            fill_identity(data, actor=req.actor, now=req.now, workspace=req.workspace)
        )
    except ValueError as exc:
        raise BadModelOutput("model output is not a valid ClientBatch", detail=str(exc)[:300]) from exc
    ctx = ParseContext(actor=req.actor, now=req.now, tz=req.time_zone)
    structured = StructuredParser(ctx).parse(line).messages
    cpack = ContextPack(pack) if pack else None
    batch, notes = postprocess(batch, structured, cpack, ctx, line)
    batch, nums = numbers_from_parser(fill_from_parser(batch, structured), structured)
    g = Grounding()
    g.add_pack(pack)
    g.item_keys |= typed_keys(line)
    g.people |= typed_handles(line)
    batch, more = enforce(batch, g)
    return batch, notes + nums + more, comp


async def translate(deps: Deps, req: TaskRequest) -> TaskResponse:
    lines = req.all_lines()
    if not lines:
        raise BadModelOutput("no line to translate")
    run = new_run("translate", req, req.options.model_alias or "translator")
    try:
        batch, notes, comp = await translate_line(deps, req, lines[0])
        run.model, run.cost_usd = comp.model, comp.cost_usd
        if deps.cfg.log_raw_input:  # reporter text stays out of stored traces unless explicitly enabled
            run.meta["raw_input"] = lines[0]
        await finish(deps, run)
    except Exception:
        await finish(deps, run, "error")
        raise
    return respond(
        run,
        batch=batch.model_dump(mode="json"),
        output=None,
        confidence=mean_confidence(batch),
        warnings=notes,
    )
