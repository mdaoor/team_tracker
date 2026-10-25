"""Phase 8 API: policy context schema, policy templates and policies (object model §8).

Violations live in `governance_violations`; habits, metric snapshots, notifications and the admin sweep in
`governance_habits`. The lifecycle logic is `tt_server.governance.policies`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import tt_cel
from fastapi import APIRouter
from pydantic import BaseModel, Field
from tt_schema.enums import PolicyCategory, PolicyTrigger, ScopeType, Severity, TargetType

from tt_server.api.authz import check
from tt_server.api.deps import CurrentActor, Uow
from tt_server.governance import policies, service

router = APIRouter(prefix="/api/v1/w/{ws}", tags=["governance"])


# ---------------------------------------------------------------- context schema + templates
@router.get("/meta/policy-context")
async def policy_context() -> dict[str, Any]:
    """The context schema v1 the editor type-checks against: roots, object fields and registered functions with their docs."""
    return {
        "version": tt_cel.VERSION, "roots": tt_cel.ROOTS, "objects": tt_cel.OBJECTS,
        "functions": [{"name": f.name, "receiver": f.receiver, "namespace": f.namespace, "args": list(f.args), "returns": f.returns, "doc": f.doc} for f in tt_cel.FUNCTIONS],
    }  # fmt: skip


@router.get("/policy-templates")
async def list_templates(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.read")
    rows = await policies.list_templates(uow, actor.ws.id)
    return {"data": [service.template_view(r) for r in rows], "next_cursor": None}


class TemplateIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str
    description: str | None = None
    category: PolicyCategory
    cel_source: str
    target: str = "work_item"
    trigger: str = "scheduled"
    severity: Severity = Severity.WARNING
    schedule: str | None = None
    event_types: list[str] = Field(default_factory=list)
    params: list[dict[str, Any]] = Field(default_factory=list)
    detail: str = ""
    candidate_filter: dict[str, Any] | None = None
    published_scope: str = "workspace"


@router.post("/policy-templates", status_code=201)
async def create_template(body: TemplateIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.admin")
    row = await policies.create_template(uow, actor.ws, actor.person.id, body.model_dump())
    await uow.commit()
    return service.template_view(row)


@router.post("/policy-templates/{tid}:copy", status_code=201)
async def copy_template(tid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Import a template another workspace published as `shared`: an independent copy (§14.2)."""
    check(actor, "policy.admin")
    copy = await policies.copy_template(uow, actor.ws, actor.person.id, tid)
    await uow.commit()
    return service.template_view(copy)


@router.post("/policy-templates/{tid}:publish")
async def publish_template(tid: str, actor: CurrentActor, uow: Uow, shared: bool = True) -> dict[str, Any]:
    check(actor, "policy.admin")
    row = await policies.publish_template(uow, actor.ws, actor.person.id, tid, shared=shared)
    await uow.commit()
    return service.template_view(row)


# ---------------------------------------------------------------- policies
class PolicyIn(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str = Field(min_length=1)
    description: str | None = None
    template: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    condition_cel: str | None = None
    scope_type: ScopeType = ScopeType.WORKSPACE
    scope_id: str | None = None
    target: TargetType | None = None
    trigger: PolicyTrigger | None = None
    event_types: list[str] | None = None
    schedule: str | None = None
    candidate_filter: dict[str, Any] | None = None
    severity: Severity | None = None
    category: PolicyCategory | None = None
    notify: dict[str, Any] = Field(default_factory=dict)
    auto_action: dict[str, Any] | None = None
    grace_period: str | None = None


class PolicyPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    params: dict[str, Any] | None = None
    condition_cel: str | None = None
    scope_type: ScopeType | None = None
    scope_id: str | None = None
    event_types: list[str] | None = None
    schedule: str | None = None
    candidate_filter: dict[str, Any] | None = None
    severity: Severity | None = None
    category: PolicyCategory | None = None
    notify: dict[str, Any] | None = None
    auto_action: dict[str, Any] | None = None
    grace_period: str | None = None


def _can_raw(actor: CurrentActor) -> bool:
    return actor.acl.can("policy.admin")


@router.post("/policies", status_code=201)
async def create_policy(body: PolicyIn, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.write")
    p = await policies.create_policy(
        uow, actor.ws, actor.person.id, body.model_dump(), raw_allowed=_can_raw(actor)
    )
    await uow.commit()
    return service.policy_view(p)


@router.get("/policies")
async def list_policies(actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.read")
    rows = await policies.list_policies(uow, actor.ws.id)
    return {"data": [service.policy_view(p, last) for p, last in rows], "next_cursor": None}


@router.get("/policies/{pid}")
async def get_policy(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.read")
    p = await policies.get_policy(uow, actor.ws.id, pid)
    return service.policy_view(p, await policies.last_test_run(uow, p.id))


@router.patch("/policies/{pid}")
async def patch_policy(pid: str, body: PolicyPatch, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.write")
    p = await policies.get_policy(uow, actor.ws.id, pid)
    await policies.patch_policy(
        uow, actor.ws, actor.person.id, p, body.model_dump(exclude_unset=True), raw_allowed=_can_raw(actor)
    )
    await uow.commit()
    return service.policy_view(p)


@router.get("/policies/{pid}/versions")
async def policy_versions(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    """Version history, newest first, from the event log: who changed what, with the full definition at each point (restorable)."""
    check(actor, "policy.read")
    p = await policies.get_policy(uow, actor.ws.id, pid)
    return {"data": await policies.policy_versions(uow, actor.ws.id, p), "next_cursor": None}


@router.delete("/policies/{pid}", status_code=204)
async def delete_policy(pid: str, actor: CurrentActor, uow: Uow) -> None:
    check(actor, "policy.write")
    p = await policies.get_policy(uow, actor.ws.id, pid)
    policies.delete_policy(uow, actor.ws, actor.person.id, p)
    await uow.commit()


class TestIn(BaseModel):
    window: str = Field(default="30d", pattern=r"^\d+[dhw]$")


@router.post("/policies/{pid}:test")
async def test_policy(pid: str, actor: CurrentActor, uow: Uow, body: TestIn | None = None) -> dict[str, Any]:
    """Dry run, nothing is written except the `PolicyTestRun`. `scheduled`: the current candidates. `on_event`: the targets that had a
    matching event inside the window, judged on their *current* state (an approximation; ADR-0013)."""
    check(actor, "policy.write")
    p = await policies.get_policy(uow, actor.ws.id, pid)
    out = await policies.dry_run(uow, actor.ws, actor.person.id, p, (body or TestIn()).window)
    await uow.commit()
    return out


@router.post("/policies/{pid}:activate")
async def activate_policy(pid: str, actor: CurrentActor, uow: Uow, force: bool = False) -> dict[str, Any]:
    check(actor, "policy.write")
    p = await policies.get_policy(uow, actor.ws.id, pid)
    last = await policies.activate_policy(
        uow, actor.ws, actor.person.id, p, force=force and actor.acl.is_admin
    )
    await uow.commit()
    return service.policy_view(p, last)


@router.post("/policies/{pid}:deactivate")
async def deactivate_policy(pid: str, actor: CurrentActor, uow: Uow) -> dict[str, Any]:
    check(actor, "policy.write")
    p = await policies.get_policy(uow, actor.ws.id, pid)
    await policies.deactivate_policy(uow, actor.ws, actor.person.id, p)
    await uow.commit()
    return service.policy_view(p)


@router.post("/policies/{pid}:run")
async def run_policy_now(
    pid: str, actor: CurrentActor, uow: Uow, at: datetime | None = None
) -> dict[str, Any]:
    """Evaluate an active policy right now (admin): opens and auto-resolves violations like a scheduled run."""
    check(actor, "policy.admin")
    p = await policies.get_policy(uow, actor.ws.id, pid)
    out = await policies.run_policy_now(uow, actor.ws, p, at=at)
    await uow.commit()
    return out
