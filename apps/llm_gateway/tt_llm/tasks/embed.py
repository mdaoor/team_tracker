"""`POST /v1/embed` -- optional second signal for fuzzy matching."""

from __future__ import annotations

from tt_llm.routing import BadModelOutput
from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.tasks.base import Deps, finish, new_run, respond

MAX_TEXTS = 256


async def embed(deps: Deps, req: TaskRequest) -> TaskResponse:
    if not req.texts or len(req.texts) > MAX_TEXTS:
        raise BadModelOutput(f"send 1..{MAX_TEXTS} texts")
    run = new_run("embed", req, "embedder")
    vecs, model = await deps.router.embed(req.texts, workspace=req.workspace or "-")
    run.model = model
    await finish(deps, run)
    return respond(run, output={"vectors": vecs, "dim": len(vecs[0]) if vecs else 0})
