"""`POST /v1/policy/author` -- plain language -> policy draft.

Validation tools arrive with Phase 8; until then the answer is honest about being unvalidated."""

from __future__ import annotations

from tt_llm.agent.loop import run_agent
from tt_llm.agent.registry import build_registry
from tt_llm.guards import Grounding, wrap_untrusted
from tt_llm.routing import BadModelOutput
from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.tasks.base import Deps, new_run, respond, system_prompt

_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ["template", "cel"]},
        "template": {"type": ["string", "null"]},
        "params": {"type": "object"},
        "cel": {"type": ["string", "null"]},
        "validated": {"type": "boolean"},
        "dry_run": {"type": "object"},
        "notes": {"type": "string"},
    },
    "required": ["mode"],
}


async def policy_author(deps: Deps, req: TaskRequest, core_token: str | None) -> TaskResponse:
    if not req.request:
        raise BadModelOutput("request text is required")
    run = new_run("policy_author", req, "reasoner")
    core = deps.core_factory(req.workspace, core_token, run.id) if core_token else None
    result = await run_agent(
        router=deps.router,
        storage=deps.storage,
        cfg=deps.cfg,
        run=run,
        system=system_prompt(deps, "policy_author"),
        user=wrap_untrusted("reporter_text", req.request),
        tools=build_registry(core, req.pack or None),
        allowed=["policy_check", "policy_test", "list_dimension_values"],
        output_schema=_SCHEMA,
        grounding=Grounding(),
        alias="reasoner",
        privacy_mode=req.options.privacy_mode,
    )
    out = dict(result.output)
    checked = any(
        s.kind == "tool"
        and s.name == "policy_check"
        and isinstance(s.output, dict)
        and "error" not in s.output
        for s in run.steps
    )
    out["validated"] = (
        bool(out.get("validated")) and checked
    )  # the model cannot claim a validation that never ran
    if core is not None:
        await core.aclose()
    return respond(run, output=out, warnings=result.warnings)
