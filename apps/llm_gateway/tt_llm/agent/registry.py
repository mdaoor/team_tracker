"""Tool registry: JSON-schema'd functions the agent may call, read-only by default (Phase 2b §5)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from tt_llm.core_client import CoreClient

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler
    write: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


_S = {"type": "string"}


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def schemas(self, allowed: list[str]) -> list[dict[str, Any]]:
        return [self.tools[n].schema() for n in allowed if n in self.tools]

    def validate_args(self, name: str, args: dict[str, Any]) -> str | None:
        try:
            jsonschema.validate(args, self.tools[name].parameters)
        except jsonschema.ValidationError as exc:
            return str(exc.message)
        return None

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        return await self.tools[name].handler(args)


def truncate(result: Any, max_chars: int) -> Any:
    """Keep tool results inside the size budget; say so explicitly instead of cutting silently."""
    text = json.dumps(result, default=str)
    if len(text) <= max_chars:
        return result
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        data = list(result["data"])
        while data and len(json.dumps({**result, "data": data}, default=str)) > max_chars:
            data.pop()
        return {**result, "data": data, "truncated": True}
    if isinstance(result, list):
        data = list(result)
        while data and len(json.dumps(data, default=str)) > max_chars:
            data.pop()
        return {"data": data, "truncated": True}
    return {"truncated": True, "text": text[:max_chars]}


READ_TOOLS = (
    "get_context_pack",
    "search_items",
    "get_item",
    "resolve_project",
    "glossary_lookup",
    "list_people",
    "list_milestones",
    "list_iterations",
    "list_stages",
    "list_risks",
    "list_dimension_values",
    "recent_activity",
    "policy_check",
    "policy_test",
)
WRITE_TOOLS = ("post_capture_batch", "resolve_review_item", "create_classification")


def build_registry(core: CoreClient | None, pack: dict[str, Any] | None = None) -> ToolRegistry:
    """All tools bound to one caller. With `pack` supplied, pack-derived lookups cost no network call."""
    reg = ToolRegistry()

    async def need() -> CoreClient:
        if core is None:
            raise RuntimeError("no core server configured for this request")
        return core

    async def get_context_pack(_: dict[str, Any]) -> Any:
        return pack if pack else await (await need()).get("/context-pack")

    async def search_items(a: dict[str, Any]) -> Any:
        return await (await need()).get(
            "/items/search",
            q=a["query"],
            project=a.get("project"),
            assignee=a.get("assignee"),
            state_category=a.get("state_category"),
            limit=a.get("limit", 8),
        )

    async def get_item(a: dict[str, Any]) -> Any:
        return await (await need()).get(f"/items/{a['key']}")

    async def resolve_project(a: dict[str, Any]) -> Any:
        text = a["text"].strip().lower()
        for p in (pack or {}).get("projects", []):
            if text in {p["key"].lower(), p["name"].lower(), *(x.lower() for x in p.get("aliases", []))}:
                return {"project": p["key"], "method": "pack"}
        gl = (pack or {}).get("glossary", {})
        for phrase, target in gl.items():
            if phrase.lower() == text and str(target).startswith("PROJECT:"):
                return {"project": str(target).split(":", 1)[1], "method": "glossary"}
        if core is not None:
            rows = (await core.get("/projects")).get("data", [])
            for p in rows:
                if text in {p["key"].lower(), p["name"].lower()}:
                    return {"project": p["key"], "method": "projects"}
        return {"project": None}

    async def glossary_lookup(a: dict[str, Any]) -> Any:
        gl = (pack or {}).get("glossary") or {}
        hit = {k: v for k, v in gl.items() if k.lower() == a["phrase"].strip().lower()}
        if hit or core is None:
            return {"matches": hit}
        rows = (await core.get("/glossary")).get("data", [])
        return {
            "matches": {
                r["phrase"]: f"{r['resolves_to_type'].upper()}:{r['resolves_to_id']}"
                for r in rows
                if r["phrase"].lower() == a["phrase"].strip().lower()
            }
        }  # noqa: E501

    async def list_people(a: dict[str, Any]) -> Any:
        if pack and pack.get("people") and not a.get("team"):
            return {"data": pack["people"]}
        return await (await need()).get("/people")

    async def list_milestones(a: dict[str, Any]) -> Any:
        rows = (await (await need()).get("/milestones")).get("data", [])
        return {
            "data": [
                r for r in rows if r.get("project_key", r.get("project")) in (None, a["project"].upper())
            ]
        }

    async def list_iterations(a: dict[str, Any]) -> Any:
        rows = (await (await need()).get("/iterations")).get("data", [])
        return {
            "data": [
                r for r in rows if r.get("project_key", r.get("project")) in (None, a["project"].upper())
            ]
        }

    async def list_stages(a: dict[str, Any]) -> Any:
        return await (await need()).get(f"/processes/{a['process']}/board")

    async def list_risks(a: dict[str, Any]) -> Any:
        return await (await need()).get("/risks", project=a.get("project"))

    async def list_dimension_values(a: dict[str, Any]) -> Any:
        return await (await need()).get(f"/dimensions/{a['dimension']}/values")

    async def recent_activity(a: dict[str, Any]) -> Any:
        return await (await need()).get("/activities", person=a.get("actor", "me"), limit=a.get("limit", 20))

    async def not_available(_: dict[str, Any]) -> Any:
        return {"error": "not_available", "detail": "the policy engine ships with Phase 8"}

    async def post_capture_batch(a: dict[str, Any]) -> Any:
        return await (await need()).post("/capture/batches", a["batch"])

    async def resolve_review_item(a: dict[str, Any]) -> Any:
        return await (await need()).post(
            "/review-queue/resolve", {"ref": a["ref"], "choice": a.get("choice", {})}
        )

    async def create_classification(a: dict[str, Any]) -> Any:
        return await (await need()).post("/classifications", a)

    for t in (
        Tool(
            "get_context_pack",
            "The manager-published context for the current person.",
            _obj({}),
            get_context_pack,
        ),
        Tool(
            "search_items",
            "Fuzzy-search work items by title; returns key, title and a similarity score.",
            _obj(
                {
                    "query": _S,
                    "project": _S,
                    "assignee": _S,
                    "state_category": _S,
                    "limit": {"type": "integer"},
                },
                ["query"],
            ),
            search_items,
        ),
        Tool("get_item", "One work item by key.", _obj({"key": _S}, ["key"]), get_item),
        Tool(
            "resolve_project", "Map a phrase to a project key.", _obj({"text": _S}, ["text"]), resolve_project
        ),
        Tool(
            "glossary_lookup",
            "Look a phrase up in the glossary.",
            _obj({"phrase": _S}, ["phrase"]),
            glossary_lookup,
        ),
        Tool("list_people", "People in the workspace.", _obj({"team": _S}), list_people),
        Tool(
            "list_milestones", "Milestones of a project.", _obj({"project": _S}, ["project"]), list_milestones
        ),
        Tool(
            "list_iterations", "Iterations of a project.", _obj({"project": _S}, ["project"]), list_iterations
        ),
        Tool("list_stages", "Stages of a process.", _obj({"process": _S}, ["process"]), list_stages),
        Tool("list_risks", "Risks, optionally of one project.", _obj({"project": _S}), list_risks),
        Tool(
            "list_dimension_values",
            "Values of a dimension.",
            _obj({"dimension": _S}, ["dimension"]),
            list_dimension_values,
        ),
        Tool(
            "recent_activity",
            "Recent activities of a person.",
            _obj({"actor": _S, "limit": {"type": "integer"}}),
            recent_activity,
        ),
        Tool("policy_check", "Validate a CEL policy expression.", _obj({"cel": _S}, ["cel"]), not_available),
        Tool(
            "policy_test",
            "Dry-run a policy over a window.",
            _obj({"policy": _S, "window": _S}),
            not_available,
        ),
        Tool(
            "post_capture_batch",
            "Send a ClientBatch to the capture API.",
            _obj({"batch": {"type": "object"}}, ["batch"]),
            post_capture_batch,
            True,
        ),
        Tool(
            "resolve_review_item",
            "Resolve a review row.",
            _obj({"ref": _S, "choice": {"type": "object"}}, ["ref"]),
            resolve_review_item,
            True,
        ),
        Tool(
            "create_classification",
            "Create an unconfirmed classification.",
            _obj({"target_id": _S, "dimension": _S, "value": _S}, ["target_id", "dimension", "value"]),
            create_classification,
            True,
        ),
    ):
        reg.add(t)
    return reg
