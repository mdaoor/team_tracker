"""Generates `packages/client_core/tests/fixtures/catalogue.json`, a stand-in for `GET /commands/catalogue`.

The server's real catalogue replaces the fixture at integration; every client test iterates whatever the fixture holds.
Usage: `python tools/gen_command_catalogue_fixture.py [out.json]`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

S: dict[str, Any] = {"type": "string"}


def s(desc: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"type": "string", **({"description": desc} if desc else {}), **kw}


def enum(*v: str, desc: str | None = None) -> dict[str, Any]:
    return {"type": "string", "enum": list(v), **({"description": desc} if desc else {})}


def arr(item: dict[str, Any] | None = None, desc: str | None = None, **kw: Any) -> dict[str, Any]:
    return {"type": "array", "items": item or S, **({"description": desc} if desc else {}), **kw}


def person(desc: str = "@handle") -> dict[str, Any]:
    return s(desc, format="person")


def project(desc: str = "project key") -> dict[str, Any]:
    return s(desc, format="project")


def team(desc: str = "team name") -> dict[str, Any]:
    return s(desc, format="team")


def item(desc: str = "item key, e.g. WEB-101") -> dict[str, Any]:
    return s(desc, format="item", pattern=r"^[A-Z][A-Z0-9]+-\d+$")


def items(desc: str = "item keys") -> dict[str, Any]:
    return arr(item(), desc, minItems=1)


def integer(desc: str | None = None, **k: Any) -> dict[str, Any]:
    return {"type": "integer", **({"description": desc} if desc else {}), **k}


def boolean(desc: str | None = None) -> dict[str, Any]:
    return {"type": "boolean", **({"description": desc} if desc else {})}


def obj(desc: str | None = None) -> dict[str, Any]:
    return {"type": "object", **({"description": desc} if desc else {})}


def op(
    name: str,
    group: str,
    desc: str,
    props: dict[str, Any],
    required: list[str],
    perm: str,
    ex_args: dict[str, Any],
    ex_text: str,
    *,
    preview: bool = False,
    destructive: bool = False,
    anyof: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }
    if anyof:
        schema["anyOf"] = anyof
    return {
        "op": name,
        "group": group,
        "description": desc,
        "permission": perm,
        "preview": preview,
        "destructive": destructive,
        "args_schema": schema,
        "example": {"args": ex_args, "text": ex_text},
    }


PRIO = enum("lowest", "low", "medium", "high", "critical")
METH = enum("scrum", "kanban", "waterfall", "hybrid")
SEV = enum("low", "medium", "high", "critical")
DATE = s(format="date")


def build() -> list[dict[str, Any]]:
    o: list[dict[str, Any]] = []
    a = o.append
    # projects
    a(op("project.create", "projects", "Create a project", {"name": s("project name", minLength=1), "key": s("short key", pattern="^[A-Z][A-Z0-9]{1,9}$"), "process": s("process template"), "product": s("product"), "team": team(), "methodology": METH}, ["name", "key"], "project.create", {"name": "Payments", "key": "PAY", "methodology": "scrum"}, "new project Payments key PAY scrum", preview=True))  # fmt: skip
    a(op("project.update", "projects", "Update project fields", {"project": project(), "name": s(), "process": s(), "product": s(), "methodology": METH}, ["project"], "project.write", {"project": "WEB", "name": "Website 2"}, "rename project WEB to Website 2"))  # fmt: skip
    a(op("project.add_member", "projects", "Add a person to a project", {"project": project(), "person": person(), "role": enum("member", "lead", "viewer", "owner")}, ["project", "person", "role"], "project.write", {"project": "WEB", "person": "@sara", "role": "member"}, "add @sara to WEB as member", preview=True))  # fmt: skip
    a(op("project.plan.create", "projects", "Create a top-down plan (phases, milestones, deliverables)", {"project": project(), "plan": obj("plan payload")}, ["project", "plan"], "project.write", {"project": "WEB", "plan": {"phases": [{"name": "Build"}]}}, "plan WEB with a Build phase", preview=True))  # fmt: skip
    a(op("project.baseline.capture", "projects", "Capture a baseline of the current plan", {"project": project(), "name": s("baseline name")}, ["project"], "project.write", {"project": "WEB", "name": "Q4 baseline"}, "baseline WEB as Q4 baseline"))  # fmt: skip
    # items
    a(op("item.create", "items", "Create an overlay item (epic, deliverable, task)", {"title": s(minLength=1), "type": enum("epic", "story", "task", "bug", "deliverable", "milestone"), "project": project(), "parent": item("parent item key"), "priority": PRIO, "description": s()}, ["title", "type", "project"], "item.write_overlay", {"title": "Checkout redesign", "type": "epic", "project": "WEB"}, "new epic Checkout redesign under WEB", preview=True))  # fmt: skip
    a(op("item.update", "items", "Update item fields", {"key": item(), "fields": obj("fields to change")}, ["key", "fields"], "item.write_overlay", {"key": "WEB-1", "fields": {"title": "Better title"}}, "rename WEB-1 to Better title"))  # fmt: skip
    a(op("item.reparent", "items", "Move items under a new parent", {"keys": items(), "parent": item("new parent key")}, ["keys", "parent"], "item.write_overlay", {"keys": ["WEB-2", "WEB-3"], "parent": "WEB-1"}, "move WEB-2 and WEB-3 under WEB-1", preview=True))  # fmt: skip
    a(op("item.merge", "items", "Merge several items into one (sources are soft-deleted)", {"sources": arr(item(), "items to merge", minItems=2), "target": item("keep this item"), "new_title": s("or create a new merged item with this title"), "allow_cross_project": boolean()}, ["sources"], "item.write_overlay", {"sources": ["WEB-101", "WEB-105"], "target": "WEB-101"}, "merge WEB-101 and WEB-105", preview=True, destructive=True, anyof=[{"required": ["target"]}, {"required": ["new_title"]}]))  # fmt: skip
    a(op("item.split", "items", "Split an item into several", {"key": item(), "titles": arr(s(minLength=1), "titles of the new items", minItems=2)}, ["key", "titles"], "item.write_overlay", {"key": "WEB-7", "titles": ["Design", "Build"]}, "split WEB-7 into Design and Build", preview=True, destructive=True))  # fmt: skip
    a(op("item.assign", "items", "Assign a person to an item", {"key": item(), "person": person(), "role": enum("owner", "contributor", "reviewer")}, ["key", "person", "role"], "item.write_overlay", {"key": "WEB-1", "person": "@sara", "role": "owner"}, "assign WEB-1 to @sara"))  # fmt: skip
    a(op("item.set_priority", "items", "Set priority", {"key": item(), "priority": PRIO}, ["key", "priority"], "item.write_overlay", {"key": "WEB-1", "priority": "high"}, "WEB-1 priority high"))  # fmt: skip
    a(op("item.set_impact", "items", "Set business impact", {"key": item(), "impact": enum("low", "medium", "high")}, ["key", "impact"], "item.write_overlay", {"key": "WEB-1", "impact": "high"}, "WEB-1 impact high"))  # fmt: skip
    a(op("item.set_weight", "items", "Set weight for progress roll-up", {"key": item(), "weight": integer(minimum=0, maximum=100)}, ["key", "weight"], "item.write_overlay", {"key": "WEB-1", "weight": 5}, "WEB-1 weight 5"))  # fmt: skip
    a(op("item.set_stage", "items", "Set the stage of one item", {"key": item(), "stage": s("stage name")}, ["key", "stage"], "item.write_overlay", {"key": "WEB-1", "stage": "build"}, "WEB-1 stage build"))  # fmt: skip
    a(op("item.set_state", "items", "Set workflow state", {"key": item(), "state": enum("todo", "doing", "blocked", "done", "cancelled")}, ["key", "state"], "item.write_overlay", {"key": "WEB-1", "state": "blocked"}, "WEB-1 is blocked"))  # fmt: skip
    a(op("item.set_dates", "items", "Set planned dates", {"key": item(), "start": DATE, "end": DATE}, ["key"], "item.write_overlay", {"key": "WEB-1", "end": "2026-11-30"}, "WEB-1 due 2026-11-30"))  # fmt: skip
    a(op("item.set_estimate", "items", "Set the estimate", {"key": item(), "value": {"type": "number", "minimum": 0}, "unit": enum("hours", "days", "points")}, ["key", "value", "unit"], "item.write_overlay", {"key": "WEB-1", "value": 3, "unit": "days"}, "WEB-1 estimate 3 days"))  # fmt: skip
    a(op("item.link", "items", "Link two items", {"from": item("from item"), "to": item("to item"), "type": enum("blocks", "relates", "duplicates", "depends_on")}, ["from", "to", "type"], "item.write_overlay", {"from": "WEB-1", "to": "WEB-2", "type": "blocks"}, "WEB-1 blocks WEB-2"))  # fmt: skip
    a(op("item.tag", "items", "Add or remove tags on many items", {"keys": items(), "add": arr(s(), "tags to add"), "remove": arr(s(), "tags to remove")}, ["keys"], "item.write_overlay", {"keys": ["DATA-1", "DATA-2"], "add": ["q4"]}, "tag DATA-1 DATA-2 #q4", preview=True))  # fmt: skip
    # teams & people
    a(op("team.create", "teams", "Create a team (org, virtual or ad hoc)", {"name": s(minLength=1), "kind": enum("org", "virtual", "ad_hoc"), "lead": person(), "members": arr(person(), "members")}, ["name", "kind"], "team.write", {"name": "payments", "kind": "virtual", "lead": "@mahmoud", "members": ["@sara", "@ali"]}, "create virtual team payments with @sara @ali led by me", preview=True))  # fmt: skip
    a(op("team.add_members", "teams", "Add members to a team", {"team": team(), "members": arr(person(), minItems=1)}, ["team", "members"], "team.write", {"team": "payments", "members": ["@omar"]}, "add @omar to team payments", preview=True))  # fmt: skip
    a(op("team.remove_members", "teams", "Remove members from a team", {"team": team(), "members": arr(person(), minItems=1)}, ["team", "members"], "team.write", {"team": "payments", "members": ["@ali"]}, "remove @ali from team payments", preview=True, destructive=True))  # fmt: skip
    a(op("reporting_line.set", "teams", "Set a person's manager", {"person": person(), "manager": person(), "kind": enum("solid", "dotted", "functional"), "scope": s("project or team scope")}, ["person", "manager", "kind"], "reporting.write", {"person": "@sara", "manager": "@mahmoud", "kind": "solid"}, "@sara reports to me", preview=True, destructive=True))  # fmt: skip
    # dimensions & tags
    a(op("tag.create", "tags", "Create a tag", {"name": s(minLength=1), "description": s()}, ["name"], "tag.write", {"name": "q4"}, "new tag q4"))  # fmt: skip
    a(op("tag.merge", "tags", "Merge tags", {"sources": arr(s(), minItems=1), "target": s()}, ["sources", "target"], "tag.write", {"sources": ["q-4"], "target": "q4"}, "merge tag q-4 into q4", preview=True))  # fmt: skip
    a(op("dimension.value.add", "tags", "Add a value to a dimension", {"dimension": s("dimension key"), "value": s(minLength=1)}, ["dimension", "value"], "dimension.write", {"dimension": "customer", "value": "ACME"}, "add value ACME to dimension customer"))  # fmt: skip
    a(op("classify", "tags", "Classify a target by a dimension value", {"target": item("item key"), "dimension": s(), "value": s()}, ["target", "dimension", "value"], "item.write_overlay", {"target": "WEB-1", "dimension": "customer", "value": "ACME"}, "classify WEB-1 customer ACME"))  # fmt: skip
    # process & risk
    a(op("risk.create", "process", "Open a risk", {"title": s(minLength=1), "project": project(), "severity": SEV, "owner": person()}, ["title", "project"], "risk.write", {"title": "Vendor slips", "project": "WEB", "severity": "high"}, "new risk Vendor slips on WEB high"))  # fmt: skip
    a(op("risk.link", "process", "Link a risk to items", {"risk": s("risk key"), "keys": items()}, ["risk", "keys"], "risk.write", {"risk": "WEB-R7", "keys": ["WEB-1"]}, "link risk WEB-R7 to WEB-1"))  # fmt: skip
    a(op("risk.action.add", "process", "Add a mitigation action", {"risk": s("risk key"), "action": s(minLength=1), "owner": person(), "due": DATE}, ["risk", "action"], "risk.write", {"risk": "WEB-R7", "action": "Ask vendor for date"}, "risk WEB-R7 action Ask vendor for date"))  # fmt: skip
    a(op("approval.decide", "process", "Approve or reject a pending approval", {"approval": s("approval id"), "decision": enum("approve", "reject"), "comment": s()}, ["approval", "decision"], "approval.decide", {"approval": "AP-12", "decision": "approve", "comment": "ok"}, "approve AP-12"))  # fmt: skip
    a(op("stage.move", "process", "Move items to a stage with a reason", {"keys": items(), "stage": s("stage name"), "reason": s("why")}, ["keys", "stage", "reason"], "item.write_overlay", {"keys": ["WEB-1", "WEB-2"], "stage": "test", "reason": "failed QA"}, "move WEB-1 WEB-2 to test because failed QA", preview=True))  # fmt: skip
    # governance
    a(op("review.resolve", "governance", "Resolve a review-queue row", {"row": s("review row id"), "choice": s("one of the row's choices")}, ["row", "choice"], "review.resolve", {"row": "RV-3", "choice": "accept"}, "resolve RV-3 accept", preview=True))  # fmt: skip
    a(op("review.bulk", "governance", "Resolve many review rows with their defaults", {"rows": arr(s(), minItems=1), "choice": s()}, ["rows"], "review.resolve", {"rows": ["RV-3", "RV-4"]}, "accept all review rows", preview=True))  # fmt: skip
    a(op("violation.ack", "governance", "Acknowledge a violation", {"id": s("violation id"), "note": s()}, ["id"], "violation.manage", {"id": "V-9"}, "ack violation V-9"))  # fmt: skip
    a(op("violation.resolve", "governance", "Mark a violation resolved", {"id": s(), "note": s()}, ["id"], "violation.manage", {"id": "V-9", "note": "fixed"}, "resolve violation V-9"))  # fmt: skip
    a(op("violation.waive", "governance", "Waive a violation (reason required)", {"id": s(), "reason": s(minLength=1)}, ["id", "reason"], "violation.waive", {"id": "V-9", "reason": "holiday week"}, "waive violation V-9 because holiday week"))  # fmt: skip
    a(op("habit.coach_note", "governance", "Write a coaching note on a habit", {"habit": s("habit key"), "person": person(), "note": s(minLength=1)}, ["habit", "note"], "habit.coach", {"habit": "daily-log", "person": "@sara", "note": "great streak"}, "coach note on daily-log for @sara: great streak"))  # fmt: skip
    a(op("habit.set_status", "governance", "Set a habit's status", {"habit": s(), "status": enum("active", "paused", "retired")}, ["habit", "status"], "habit.coach", {"habit": "daily-log", "status": "paused"}, "pause habit daily-log"))  # fmt: skip
    a(op("policy.activate", "governance", "Activate a policy", {"policy": s("policy key")}, ["policy"], "policy.manage", {"policy": "wip-limit"}, "activate policy wip-limit", preview=True))  # fmt: skip
    a(op("policy.deactivate", "governance", "Deactivate a policy", {"policy": s()}, ["policy"], "policy.manage", {"policy": "wip-limit"}, "deactivate policy wip-limit", preview=True, destructive=True))  # fmt: skip
    # iterations & milestones
    a(op("iteration.create", "iterations", "Create an iteration", {"name": s(minLength=1), "project": project(), "start": DATE, "end": DATE, "goal": s()}, ["name", "project"], "iteration.manage", {"name": "Sprint 15", "project": "WEB", "start": "2026-11-02", "end": "2026-11-13"}, "new sprint Sprint 15 on WEB 2026-11-02 to 2026-11-13"))  # fmt: skip
    a(op("iteration.start", "iterations", "Start an iteration", {"iteration": s("iteration name"), "project": project()}, ["iteration"], "iteration.manage", {"iteration": "Sprint 15", "project": "WEB"}, "start Sprint 15"))  # fmt: skip
    a(op("iteration.close", "iterations", "Close an iteration", {"iteration": s(), "project": project(), "carry_over": boolean("move open items to the next iteration")}, ["iteration"], "iteration.manage", {"iteration": "Sprint 14", "project": "WEB", "carry_over": True}, "close Sprint 14", preview=True))  # fmt: skip
    a(op("milestone.create", "iterations", "Create a milestone", {"name": s(minLength=1), "project": project(), "due": DATE}, ["name", "project"], "milestone.manage", {"name": "Beta", "project": "WEB", "due": "2026-12-01"}, "new milestone Beta on WEB due 2026-12-01"))  # fmt: skip
    a(op("milestone.complete", "iterations", "Complete a milestone", {"milestone": s("milestone name"), "project": project()}, ["milestone"], "milestone.manage", {"milestone": "Beta", "project": "WEB"}, "complete milestone Beta"))  # fmt: skip
    a(op("item.move_to_iteration", "iterations", "Move items into an iteration", {"keys": items(), "iteration": s("iteration name")}, ["keys", "iteration"], "item.write_overlay", {"keys": ["WEB-1", "WEB-2"], "iteration": "Sprint 15"}, "move WEB-1 WEB-2 to Sprint 15", preview=True))  # fmt: skip
    # personal (any role)
    a(op("dailylog.confirm", "personal", "Confirm my daily log", {"log": s("daily log id (default: today)")}, [], "self", {}, "confirm my day"))  # fmt: skip
    a(op("context_note.add", "personal", "Add a context note", {"target": s("item key, project key or person handle"), "note": s(minLength=1)}, ["target", "note"], "self", {"target": "WEB-1", "note": "waiting on legal"}, "note on WEB-1: waiting on legal"))  # fmt: skip
    return o


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "packages/client_core/tests/fixtures/catalogue.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    ops = build()
    out.write_text(json.dumps({"etag": '"cat-1"', "ops": ops}, indent=1) + "\n")
    print(f"{len(ops)} ops -> {out}")  # noqa: T201
