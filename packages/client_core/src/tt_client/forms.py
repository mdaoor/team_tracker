"""Manual mode for management commands: forms generated FROM an op's JSON Schema (plan 16, "Forms from schema").

`fields_for(op_meta, pack)` is a pure "fields spec" (string / enum / array / integer / number / boolean / json, plus pickers for
person / project / team / item backed by the ContextPack). The CLI renders it with questionary; the desktop and Slack render the
same spec in their own widgets. `build_command` turns answers into a schema-valid `ClientCommand` without any network.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from tt_schema.client import ClientCommand

from tt_client.catalogue import Issue, OpMeta
from tt_client.commands import new_command
from tt_client.pack import ContextPack

PICKERS = ("person", "project", "team", "item")
# Conservative property-name hints for schemas that carry no `format` / `x-picker` (the server may or may not add them).
_NAME_HINTS = {
    "person": "person", "manager": "person", "lead": "person", "owner": "person", "assignee": "person",
    "members": "person", "reviewer": "person",
    "project": "project", "team": "team",
    "key": "item", "keys": "item", "parent": "item",
}  # fmt: skip


@dataclass
class Choice:
    value: str
    label: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"value": self.value, "label": self.label or self.value}


@dataclass
class FieldSpec:
    name: str
    label: str
    kind: str  # text | enum | integer | number | boolean | date | json
    required: bool = False
    multi: bool = False  # array of `kind`
    description: str = ""
    picker: str | None = None  # person | project | team | item
    choices: list[Choice] = field(default_factory=list)
    strict: bool = False  # True: only `choices` are valid (enums); pickers are suggestions + free text
    default: Any = None
    constraints: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "required": self.required,
            "multi": self.multi,
            "description": self.description,
            "picker": self.picker,
            "choices": [c.to_dict() for c in self.choices],
            "strict": self.strict,
            "default": self.default,
            "constraints": self.constraints,
        }


class FormError(ValueError):
    def __init__(self, issues: list[Issue]) -> None:
        super().__init__("; ".join(str(i) for i in issues))
        self.issues = issues


def _humanise(name: str) -> str:
    return name.replace("_", " ").replace(".", " ").strip().capitalize()


def _picker_for(name: str, prop: dict[str, Any]) -> str | None:
    hint = prop.get("x-picker") or prop.get("format")
    if hint in PICKERS:
        return str(hint)
    if prop.get("items"):
        sub = prop["items"].get("x-picker") or prop["items"].get("format")
        if sub in PICKERS:
            return str(sub)
    if prop.get("enum") or prop.get("items", {}).get("enum"):
        return None
    return _NAME_HINTS.get(name)


def _picker_choices(picker: str | None, pack: ContextPack | None) -> list[Choice]:
    if pack is None or picker is None:
        return []
    if picker == "person":
        names = {p["handle"]: p.get("name") or "" for p in pack.data.get("people", [])}
        return [Choice(h, f"{h}  {n}".strip()) for h, n in names.items()]
    if picker == "project":
        return [Choice(p["key"], f"{p['key']}  {p['name']}") for p in pack.data.get("projects", [])]
    if picker == "team":
        return [Choice(n) for n in pack.team_names]
    if picker == "item":
        return [Choice(i["key"], f"{i['key']}  {i['title']}") for i in pack.open_items]
    return []


def _kind(prop: dict[str, Any]) -> str:
    t = prop.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if t == "integer":
        return "integer"
    if t == "number":
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "object":
        return "json"
    if prop.get("format") == "date":
        return "date"
    return "text"


def _constraints(prop: dict[str, Any]) -> dict[str, Any]:
    keys = ("minLength", "maxLength", "pattern", "minimum", "maximum", "minItems", "maxItems", "format")
    return {k: prop[k] for k in keys if k in prop}


def field_for(name: str, prop: dict[str, Any], required: bool, pack: ContextPack | None) -> FieldSpec:
    multi = prop.get("type") == "array"
    inner = prop.get("items", {}) if multi else prop
    enum = inner.get("enum")
    picker = _picker_for(name, prop)
    kind = "enum" if enum else _kind(inner)
    if multi and inner.get("type") == "object":
        kind = "json"
    spec = FieldSpec(
        name=name,
        label=str(prop.get("title") or _humanise(name)),
        kind=kind,
        required=required,
        multi=multi,
        description=str(prop.get("description") or ""),
        picker=picker,
        default=prop.get("default"),
        constraints={**_constraints(prop), **({"item": _constraints(inner)} if multi else {})},
    )
    if enum:
        spec.choices, spec.strict = [Choice(str(e)) for e in enum], True
    elif picker:
        spec.choices = _picker_choices(picker, pack)
    return spec


def fields_for(meta: OpMeta, pack: ContextPack | None = None) -> list[FieldSpec]:
    """Required fields first (schema order), then the optional ones."""
    props, required = meta.properties, set(meta.required)
    ordered = [n for n in props if n in required] + [n for n in props if n not in required]
    return [field_for(n, props[n], n in required, pack) for n in ordered]


def fields_spec(meta: OpMeta, pack: ContextPack | None = None) -> dict[str, Any]:
    """The JSON the desktop/Slack shells render (one source of truth with the CLI)."""
    return {
        "op": meta.op,
        "description": meta.description,
        "destructive": meta.destructive,
        "preview": meta.preview,
        "fields": [f.to_dict() for f in fields_for(meta, pack)],
    }


# ------------------------------------------------------------------ coercion
_EMPTY: tuple[Any, ...] = (None, "", [], {})


def _split(text: str) -> list[str]:
    return [p for p in re.split(r"[,\s]+", text.strip()) if p]


def _scalar(spec: FieldSpec, raw: Any, actor: str) -> Any:
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if spec.kind == "integer":
        return int(text)
    if spec.kind == "number":
        return float(text)
    if spec.kind == "boolean":
        return text.lower() in ("1", "true", "yes", "y", "on")
    if spec.kind == "json":
        return json.loads(text)
    if spec.picker == "person":
        if text.lower() in ("me", "i", "myself"):
            return actor
        return text if text.startswith("@") else f"@{text}"
    if spec.picker == "item":
        return text.upper() if re.fullmatch(r"[A-Za-z][A-Za-z0-9]+-\d+", text) else text
    if spec.picker == "project":
        return text.upper() if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,9}", text) else text
    return text


def coerce(
    fields: list[FieldSpec], answers: dict[str, Any], *, actor: str = ""
) -> tuple[dict[str, Any], list[Issue]]:
    """Typed `args` from raw answers (strings from a terminal, Slack or a form). Bad values become issues, not exceptions."""
    args: dict[str, Any] = {}
    issues: list[Issue] = []
    for f in fields:
        raw = answers.get(f.name)
        if raw in _EMPTY:
            continue
        try:
            if f.multi:
                parts = (
                    raw
                    if isinstance(raw, list)
                    else _split(str(raw))
                    if f.kind != "json"
                    else json.loads(str(raw))
                )
                value: Any = [_scalar(f, p, actor) for p in parts]
            else:
                value = _scalar(f, raw, actor)
        except (ValueError, TypeError) as exc:
            issues.append(Issue(f"/{f.name}", f"not a valid {f.kind}: {exc}"))
            continue
        args[f.name] = value
    return args, issues


def build_command(
    meta: OpMeta,
    answers: dict[str, Any],
    *,
    catalogue: Any,
    pack: ContextPack | None = None,
    actor: str,
    now: datetime,
    role_mode: str | None = None,
    mode: str = "manual",
    on_behalf_of: str | None = None,
) -> ClientCommand:
    """Answers -> schema-valid `ClientCommand`, offline. Raises `FormError` listing every problem."""
    fields = fields_for(meta, pack)
    args, issues = coerce(fields, answers, actor=actor)
    issues += [i for i in catalogue.validate(meta.op, args) if i.pointer not in {x.pointer for x in issues}]
    if issues:
        raise FormError(issues)
    return new_command(
        meta.op, args, actor=actor, now=now, mode=mode, role_mode=role_mode, on_behalf_of=on_behalf_of
    )


def parse_kv(pairs: list[str]) -> dict[str, str]:
    """`--arg key=value` pairs -> dict (value kept raw: `coerce` types it from the schema)."""
    out: dict[str, str] = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"--arg expects key=value, got {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = v
    return out


def missing(fields: list[FieldSpec], answers: dict[str, Any]) -> list[str]:
    return [f.name for f in fields if f.required and answers.get(f.name) in _EMPTY]


# ------------------------------------------------------------------ interactive rendering
class Prompter(Protocol):
    """Anything that can ask one question (questionary in the terminal; a scripted fake in tests)."""

    def ask(self, spec: FieldSpec, default: Any = None) -> Any: ...


class QuestionaryPrompter:  # pragma: no cover - interactive
    def ask(self, spec: FieldSpec, default: Any = None) -> Any:
        import questionary

        label = spec.label + (" *" if spec.required else "")
        if spec.kind == "boolean":
            return questionary.confirm(label, default=bool(default)).unsafe_ask()
        if spec.kind == "enum" and not spec.multi:
            return questionary.select(
                label,
                choices=[c.value for c in spec.choices] + ([] if spec.required else [""]),
                default=default,
            ).unsafe_ask()
        if spec.kind == "enum" and spec.multi:
            return questionary.checkbox(label, choices=[c.value for c in spec.choices]).unsafe_ask()
        if spec.picker and spec.choices:
            hint = " (comma separated)" if spec.multi else ""
            return questionary.autocomplete(
                label + hint, choices=[c.value for c in spec.choices], default=str(default or "")
            ).unsafe_ask()
        hint = " (comma separated)" if spec.multi else " (JSON)" if spec.kind == "json" else ""
        return questionary.text(label + hint, default=str(default or "")).unsafe_ask()


def ask_form(
    fields: list[FieldSpec],
    prompter: Prompter,
    given: dict[str, Any] | None = None,
    *,
    ask_optional: bool = False,
    on_missing: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Walk the fields: required ones always (re-asked while empty), optional ones only with `ask_optional`."""
    answers = dict(given or {})
    for f in fields:
        if f.name in answers and answers[f.name] not in _EMPTY:
            continue
        if not f.required and not ask_optional:
            continue
        for _ in range(3):
            value = prompter.ask(f, f.default)
            if value not in _EMPTY or not f.required:
                break
            if on_missing:
                on_missing(f.name)
        answers[f.name] = value
    return answers
