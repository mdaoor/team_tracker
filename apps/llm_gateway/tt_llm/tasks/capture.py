"""`POST /v1/agent/capture`: agentic capture with read tools, optional send=true (Phase 2b §2b.3)."""

from __future__ import annotations

from typing import Any

from tt_client.pack import ContextPack
from tt_client.parser.llm import postprocess
from tt_client.parser.structured import ParseContext, StructuredParser
from tt_schema.client import ClientBatch

from tt_llm.agent.loop import run_agent
from tt_llm.agent.registry import READ_TOOLS, build_registry
from tt_llm.core_client import CoreError
from tt_llm.guards import (
    Grounding,
    enforce,
    fill_from_parser,
    numbers_from_parser,
    typed_handles,
    typed_keys,
    wrap_untrusted,
)
from tt_llm.routing import BadModelOutput, GatewayError
from tt_llm.schemas import TaskRequest, TaskResponse, capture_output_schema, fill_identity
from tt_llm.tasks.base import Deps, finish, new_run, respond, system_prompt
from tt_llm.tasks.translate import mean_confidence

CAPTURE_TOOLS = [t for t in READ_TOOLS if not t.startswith("policy_")]


class CoreRequired(GatewayError):
    status, code = 400, "core_token_required"


async def agent_capture(deps: Deps, req: TaskRequest, core_token: str | None) -> TaskResponse:
    lines = req.all_lines()
    if not lines:
        raise BadModelOutput("no line to capture")
    alias = req.options.model_alias or "agent"
    run = new_run("agent_capture", req, alias)
    core = deps.core_factory(req.workspace, core_token, run.id) if core_token else None
    if req.options.send and core is None:
        raise CoreRequired("send=true needs the caller's core-server token (X-TT-Core-Token)")
    pack = dict(req.pack)
    tools = build_registry(core, pack or None)
    g = Grounding()
    if pack:
        g.add_pack(pack)
    user = (
        "\n".join(wrap_untrusted("reporter_text", ln) for ln in lines)
        + f"\nactor: {req.actor}\nnow: {req.now.isoformat()}\ntime_zone: {req.time_zone}"
    )
    result = await run_agent(
        router=deps.router,
        storage=deps.storage,
        cfg=deps.cfg,
        run=run,
        system=system_prompt(deps, "agent_capture"),
        user=user,
        tools=tools,
        allowed=CAPTURE_TOOLS,  # no write tool is offered; `send` is done by code below
        output_schema=capture_output_schema(),
        grounding=g,
        alias=alias,
        privacy_mode=req.options.privacy_mode,
    )
    out: dict[str, Any] = result.output
    try:
        batch = ClientBatch.model_validate(
            fill_identity(out["batch"], actor=req.actor, now=req.now, workspace=req.workspace)
        )
    except ValueError as exc:
        raise BadModelOutput("model output is not a valid ClientBatch", detail=str(exc)[:300]) from exc
    batch, notes = _finalise(batch, lines, req, pack, g)
    run.meta["rationale"] = out.get("rationale")  # kept in agent_runs, never sent to the core server
    if deps.cfg.log_raw_input:
        run.meta["raw_input"] = lines
    server: Any = None
    if req.options.send and core is not None:
        try:
            server = await core.post("/capture/batches", batch.model_dump(mode="json", exclude_none=True))
        except CoreError as exc:
            notes.append(f"core server refused the batch ({exc.status})")
            server = {"error": exc.body, "status": exc.status}
        finally:
            await core.aclose()
    elif core is not None:
        await core.aclose()
    await finish(deps, run, run.status if run.status != "running" else "ok")
    return respond(
        run,
        batch=batch.model_dump(mode="json"),
        output={"server": server, "tool_calls": result.tool_calls}
        if server is not None or result.tool_calls
        else None,
        confidence=mean_confidence(batch),
        rationale=out.get("rationale"),
        warnings=[*result.warnings, *notes],
    )


def _finalise(
    batch: ClientBatch, lines: list[str], req: TaskRequest, pack: dict[str, Any], g: Grounding
) -> tuple[ClientBatch, list[str]]:
    ctx = ParseContext(actor=req.actor, now=req.now, tz=req.time_zone)
    cpack = ContextPack(pack) if pack else None
    notes: list[str] = []
    if len(lines) == 1:
        structured = StructuredParser(ctx).parse(lines[0]).messages
        batch, notes = postprocess(batch, structured, cpack, ctx, lines[0])
        batch, nums = numbers_from_parser(fill_from_parser(batch, structured), structured)
        notes += nums
    else:
        per_line = [StructuredParser(ctx).parse(ln).messages for ln in lines]
        flat = [m for ms in per_line for m in ms]
        if len(flat) == len(batch.messages):  # one-to-one: let the parser's numbers win per message
            fixed = []
            for i, (msg, ln) in enumerate(zip(batch.messages, _line_for_each(per_line, lines), strict=True)):
                one, n = postprocess(batch.model_copy(update={"messages": [msg]}), [flat[i]], cpack, ctx, ln)
                fixed.extend(one.messages)
                notes += n
            batch = batch.model_copy(update={"messages": fixed})
        else:
            joined = "\n".join(lines)
            batch = batch.model_copy(
                update={"messages": [m.model_copy(update={"raw_input": joined}) for m in batch.messages]}
            )
    g.item_keys |= typed_keys(*lines)
    g.people |= typed_handles(*lines)
    batch, more = enforce(batch, g)
    return batch, notes + more


def _line_for_each(per_line: list[list[Any]], lines: list[str]) -> list[str]:
    return [ln for ms, ln in zip(per_line, lines, strict=True) for _ in ms]
