"""`POST /v1/translate/command` and `/v1/agent/command`: natural language -> `ClientCommand[]`, constrained by the catalogue.

The gateway fetches `GET /commands/catalogue` from the core server with the CALLER's token (cached per workspace by ETag), shows
the model only those ops, validates every emitted command against the op's JSON Schema, grounds people / projects / item keys
in the pack, the tool results or the person's own words, and never applies anything: the client previews, the person confirms.
An op that is not in the catalogue cannot leave this module (ADR-0052: commands may be rejected, so we do not help them be)."""

from __future__ import annotations

import json
import re
from typing import Any

from tt_client.catalogue import Catalogue, OpMeta
from tt_client.pack import ContextPack
from tt_schema.base import new_id

from tt_llm.agent.loop import run_agent
from tt_llm.agent.registry import READ_TOOLS, build_registry
from tt_llm.core_client import CoreClient, CoreError
from tt_llm.guards import Grounding, typed_handles, typed_keys, wrap_untrusted
from tt_llm.routing import BadModelOutput, GatewayError
from tt_llm.schemas import TaskRequest, TaskResponse
from tt_llm.tasks.base import Deps, finish, new_run, respond, system_prompt

COMMAND_TOOLS = [t for t in READ_TOOLS if not t.startswith("policy_")]
PICKER_NAMES = {
    "person": "person", "manager": "person", "lead": "person", "owner": "person", "assignee": "person",
    "members": "person", "reviewer": "person", "project": "project", "team": "team",
    "key": "item", "keys": "item", "parent": "item",
}  # fmt: skip
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")


class CatalogueUnavailable(GatewayError):
    status, code = 400, "catalogue_unavailable"


# ------------------------------------------------------------------ catalogue
async def load_catalogue(deps: Deps, req: TaskRequest, core: CoreClient | None) -> Catalogue:
    """ETag-cached per workspace. `facts.catalogue` (a catalogue document) is accepted for air-gapped/offline callers."""
    if req.facts.get("catalogue"):
        return Catalogue.from_doc(req.facts["catalogue"])
    ws = req.workspace or "-"
    cached = deps.catalogues.get(ws)
    if core is None:
        if cached:
            return Catalogue.from_doc(cached[1], cached[0])
        raise CatalogueUnavailable(
            "the command catalogue needs the caller's core-server token (X-TT-Core-Token)"
        )
    try:
        status, etag, body = await core.get_conditional("/commands/catalogue", cached[0] if cached else None)
    except CoreError as exc:
        if cached:
            return Catalogue.from_doc(cached[1], cached[0])
        raise CatalogueUnavailable(f"could not read the command catalogue ({exc.status})") from exc
    except Exception as exc:  # network down: serve the last copy
        if cached:
            return Catalogue.from_doc(cached[1], cached[0])
        raise CatalogueUnavailable(f"could not reach the core server: {exc}") from exc
    if status == 304 and cached:
        return Catalogue.from_doc(cached[1], cached[0])
    doc = dict(body)
    deps.catalogues[ws] = (etag or doc.get("etag"), doc)
    return Catalogue.from_doc(doc, etag or doc.get("etag"))


def _type_label(prop: dict[str, Any]) -> str:
    if prop.get("enum"):
        return "|".join(str(e) for e in prop["enum"])
    t = prop.get("type")
    if t == "array":
        return "[" + _type_label(prop.get("items", {})) + "]"
    fmt = prop.get("format") or prop.get("x-picker")
    if fmt in ("person", "project", "team", "item"):
        return str(fmt)
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    return {"integer": "int", "number": "number", "boolean": "bool", "object": "object"}.get(
        str(t), "string" if fmt != "date" else "date"
    )


def signature(meta: OpMeta) -> str:
    req = set(meta.required)
    parts = [f"{n}{'*' if n in req else ''}: {_type_label(p)}" for n, p in meta.properties.items()]
    return f"{meta.op}({', '.join(parts)})" + (" !" if meta.destructive else "")


def catalogue_prompt(cat: Catalogue) -> str:
    return "\n".join(f"- {signature(o)} - {o.description}" for o in cat)


def examples_prompt(cat: Catalogue) -> str:
    """Few-shot set per op, taken from the server's own `example` so it follows the catalogue."""
    lines = []
    for o in cat:
        ex = o.example or {}
        if ex.get("text") is not None:
            lines.append(
                f'- "{ex["text"]}" -> {json.dumps({"op": o.op, "args": ex.get("args", {})}, ensure_ascii=False)}'
            )
    return "\n".join(lines)


def command_output_schema(cat: Catalogue) -> dict[str, Any]:
    """What the model may emit: ops restricted to the catalogue's enum; args are validated per op afterwards."""
    return {
        "type": "object",
        "properties": {
            "commands": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": cat.names},
                        "args": {"type": "object"},
                        "on_behalf_of": {"type": ["string", "null"]},
                    },
                    "required": ["op", "args"],
                },
            },
            "rationale": {"type": "string"},
        },
        "required": ["commands"],
    }


# ------------------------------------------------------------------ grounding & validation
def _picker(name: str, prop: dict[str, Any], op: str = "") -> str | None:
    hint = prop.get("x-picker") or prop.get("format") or (prop.get("items") or {}).get("format")
    if hint in ("person", "project", "team", "item"):
        return str(hint)
    if prop.get("enum") or (prop.get("items") or {}).get("enum"):
        return None
    if name == "key" and op and not op.startswith("item."):
        return None  # e.g. project.create's new project key
    return PICKER_NAMES.get(name)


def _ground_value(kind: str, value: str, g: Grounding, teams: set[str], actor: str) -> str | None:
    if kind == "person":
        v = actor if value.strip().lower() == "me" else value.strip()
        v = v if v.startswith("@") else f"@{v}"
        return v if v in g.people else None
    if kind == "project":
        v = value.strip().upper()
        return v if v in g.projects else None
    if kind == "item":
        v = value.strip().upper()
        return v if v in g.item_keys or (KEY_RE.match(v) and v in g.item_keys) else None
    if kind == "team":
        return value if (not teams or value in teams or value.lower() in {t.lower() for t in teams}) else None
    return value


def ground_args(
    meta: OpMeta, args: dict[str, Any], g: Grounding, teams: set[str], actor: str, notes: list[str]
) -> dict[str, Any]:
    """Drop any person / project / item key / team the model made up; `me` becomes the actor."""
    out: dict[str, Any] = {}
    props = meta.properties
    for name, value in args.items():
        if value in (None, "", [], {}):
            continue
        prop = props.get(name)
        kind = _picker(name, prop, meta.op) if prop else None
        if prop is None or kind is None:
            out[name] = value
            continue
        if isinstance(value, list):
            kept = [x for x in (_ground_value(kind, str(v), g, teams, actor) for v in value) if x]
            if len(kept) != len(value):
                notes.append(f"{meta.op}.{name}: dropped {len(value) - len(kept)} unknown {kind}(s)")
            if kept:
                out[name] = kept
        else:
            got = _ground_value(kind, str(value), g, teams, actor)
            if got is None:
                notes.append(f"{meta.op}.{name}: dropped unknown {kind} {value!r}")
            else:
                out[name] = got
    return out


def finalise(
    raw: list[dict[str, Any]], cat: Catalogue, line: str, req: TaskRequest, g: Grounding
) -> tuple[list[dict[str, Any]], list[str]]:
    """The only exit: catalogue ops, schema-valid args, grounded entities. Everything else is dropped with a warning."""
    g.item_keys |= typed_keys(line)
    g.people |= typed_handles(line) | {req.actor}
    teams = {t["name"] for t in (req.pack or {}).get("teams", []) if t.get("name")}
    notes: list[str] = []
    out: list[dict[str, Any]] = []
    for item in raw:
        op = str(item.get("op", ""))
        meta = cat.get(op)
        if meta is None:
            notes.append(f"dropped {op!r}: not in the server's command catalogue")
            continue
        args = {k: v for k, v in dict(item.get("args") or {}).items() if k != "_meta"}
        args = ground_args(meta, args, g, teams, req.actor, notes)
        issues = cat.validate(op, args)
        if issues:
            notes.append(f"dropped {op}: " + "; ".join(str(i) for i in issues))
            continue
        behalf = item.get("on_behalf_of")
        if behalf:
            behalf = req.actor if str(behalf).lower() == "me" else str(behalf)
            if behalf not in g.people:
                notes.append(f"dropped unknown person {behalf}")
                behalf = None
        if op == "project.create" and isinstance(args.get("key"), str):
            g.projects.add(
                args["key"]
            )  # a later command in the same request may refer to the project just proposed
        out.append(
            {
                "client_id": new_id(),
                "actor": req.actor,
                "at": req.now.isoformat(),
                "op": op,
                "args": args,
                "on_behalf_of": behalf,
                "preview_only": False,
            }
        )
    return out, notes


# ------------------------------------------------------------------ tasks
def _grounding(req: TaskRequest) -> Grounding:
    g = Grounding()
    if req.pack:
        g.add_pack(req.pack)
        g.people |= {m for t in req.pack.get("teams", []) for m in t.get("members", []) if m}
    return g


def _prompt(deps: Deps, req: TaskRequest, cat: Catalogue) -> str:
    compact = ContextPack(dict(req.pack)).compact() if req.pack else {}
    compact.update(
        {
            k: req.pack[k]
            for k in ("teams", "pending_approvals", "my_projects_as_manager")
            if k in (req.pack or {})
        }
    )
    return system_prompt(
        deps,
        "translate_command",
        catalogue=catalogue_prompt(cat),
        examples=examples_prompt(cat),
        role=req.role_mode or "reporter",
        pack=json.dumps(compact, ensure_ascii=False),
    )


def _user(req: TaskRequest, line: str) -> str:
    return (
        wrap_untrusted("reporter_text", line)
        + f"\nactor: {req.actor}\nnow: {req.now.isoformat()}\ntime_zone: {req.time_zone}"
    )


async def translate_command(deps: Deps, req: TaskRequest, core_token: str | None) -> TaskResponse:
    lines = req.all_lines()
    if not lines:
        raise BadModelOutput("no request to translate")
    run = new_run("translate_command", req, req.options.model_alias or "translator")
    core = deps.core_factory(req.workspace, core_token, run.id) if core_token else None
    try:
        cat = await load_catalogue(deps, req, core)
        data, comp = await deps.router.structured(
            req.options.model_alias or "translator",
            [
                {"role": "system", "content": _prompt(deps, req, cat)},
                {"role": "user", "content": _user(req, lines[0])},
            ],
            command_output_schema(cat),
            workspace=req.workspace or "-",
            timeout=deps.cfg.limits.translate_timeout_s * 4,
            privacy_mode=req.options.privacy_mode,
        )
        run.model, run.cost_usd = comp.model, comp.cost_usd
        commands, notes = finalise(list(data.get("commands", [])), cat, lines[0], req, _grounding(req))
        if deps.cfg.log_raw_input:
            run.meta["raw_input"] = lines[0]
        await finish(deps, run)
    except Exception:
        await finish(deps, run, "error")
        raise
    finally:
        if core is not None:
            await core.aclose()
    return respond(
        run,
        output={"commands": commands, "catalogue_etag": cat.etag},
        rationale=data.get("rationale"),
        warnings=notes,
    )


async def agent_command(deps: Deps, req: TaskRequest, core_token: str | None) -> TaskResponse:
    """Like `translate_command`, but the model may call read tools (search items, list people...) to resolve names."""
    lines = req.all_lines()
    if not lines:
        raise BadModelOutput("no request to translate")
    alias = req.options.model_alias or "agent"
    run = new_run("agent_command", req, alias)
    core = deps.core_factory(req.workspace, core_token, run.id) if core_token else None
    g = _grounding(req)
    try:
        cat = await load_catalogue(deps, req, core)
        result = await run_agent(
            router=deps.router,
            storage=deps.storage,
            cfg=deps.cfg,
            run=run,
            system=_prompt(deps, req, cat),
            user=_user(req, lines[0]),
            tools=build_registry(core, dict(req.pack) or None),
            allowed=COMMAND_TOOLS,  # read tools only: nothing here can write
            output_schema=command_output_schema(cat),
            grounding=g,
            alias=alias,
            privacy_mode=req.options.privacy_mode,
        )
        out: dict[str, Any] = result.output
        commands, notes = finalise(list(out.get("commands", [])), cat, lines[0], req, g)
        run.meta["rationale"] = out.get("rationale")
        await finish(deps, run, run.status if run.status != "running" else "ok")
    except Exception:
        await finish(deps, run, "error")
        raise
    finally:
        if core is not None:
            await core.aclose()
    return respond(
        run,
        output={"commands": commands, "catalogue_etag": cat.etag, "tool_calls": result.tool_calls},
        rationale=out.get("rationale"),
        warnings=[*result.warnings, *notes],
    )
