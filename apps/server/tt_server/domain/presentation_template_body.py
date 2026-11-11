"""The template body as data (plan P7 section 7.5): the Pydantic model and JSON schema, validation with JSON pointers, parameters and instantiation."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from tt_server.domain import presentations as pres
from tt_server.domain.errors import DomainError, Invalid

SCHEMA_VERSION = 1
VARIABLES = (
    "scope.label", "scope.kind", "scope.ref", "scope.items", "scope.projects", "project.key", "project.name", "range.days",
    "range.from", "range.to", "viewer.handle", "workspace.name", "today",
)  # fmt: skip
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][\w.]*)\}")
WHEN = re.compile(r"^\s*(scope\.items|scope\.projects|scope\.plan_items)\s*(==|!=|>=|<=|>|<)\s*(\d+)\s*$")


ScopeKind = Literal["workspace", "product", "project", "team", "person"]
ALL_SCOPES: list[ScopeKind] = ["workspace", "product", "project", "team", "person"]


class Param(BaseModel):
    """A parameter a template asks for (name, type, whether required)."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: Literal["project", "product", "team", "process", "dimension", "int", "string", "date_range"] = (
        "string"
    )
    required: bool = False
    default: Any = None
    label: str | None = None


class TSlide(BaseModel):
    """A slide blueprint inside a template, with title templates and the data it requires."""

    kind: str
    title_template: str | None = None
    subtitle_template: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    requires: Literal["plan", "activity", "both"] | None = None
    render_empty: Literal["hide", "placeholder", "note"] | None = None
    notes_template: str | None = None
    layout: Literal["full", "split", "grid"] = "full"


class PostRule(BaseModel):
    """A rule applied after rendering, e.g. dropping slides when a condition holds."""

    when: str
    action: Literal["drop_slides"] = "drop_slides"
    kinds: list[str] = Field(default_factory=list)


class Theme(BaseModel):
    """Optional visual defaults of a template (palette, font, logo, footer)."""

    palette: str | None = None
    font: str | None = None
    logo_attachment_id: str | None = None
    footer: str | None = None


class TemplateBody(BaseModel):
    """The validated body of a presentation template."""

    schema_version: int = SCHEMA_VERSION
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    scope_types: list[ScopeKind] = Field(default_factory=lambda: list(ALL_SCOPES))
    params: list[Param] = Field(default_factory=list)
    theme: Theme | None = None
    slides: list[TSlide] = Field(min_length=1, max_length=60)
    post_rules: list[PostRule] = Field(default_factory=list)


def json_schema() -> dict[str, Any]:
    """JSON Schema of TemplateBody for authoring tools."""
    return TemplateBody.model_json_schema()


def _pointer(loc: tuple[Any, ...]) -> str:
    return "/" + "/".join(str(x) for x in loc) if loc else "/"


def validate_body(raw: Any) -> tuple[TemplateBody | None, list[dict[str, str]]]:
    """Schema + slide-kind + parameter checks. Errors are `{pointer, message}`; an empty list means the template is usable."""
    errors: list[dict[str, str]] = []
    try:
        body = TemplateBody.model_validate(raw)
    except ValidationError as exc:
        return None, [{"pointer": _pointer(e["loc"]), "message": e["msg"]} for e in exc.errors()]
    if body.schema_version != SCHEMA_VERSION:
        errors.append(
            {
                "pointer": "/schema_version",
                "message": f"unsupported schema_version {body.schema_version} (this server reads {SCHEMA_VERSION})",
            }
        )
    names = [p.name for p in body.params]
    for i, n in enumerate(names):
        if names.index(n) != i:
            errors.append({"pointer": f"/params/{i}/name", "message": f"duplicate parameter {n!r}"})
    known = set(names) | set(VARIABLES)
    for i, sl in enumerate(body.slides):
        if sl.kind not in pres.SLIDE_KINDS:
            errors.append(
                {
                    "pointer": f"/slides/{i}/kind",
                    "message": f"unknown slide kind {sl.kind!r}; see GET /presentation/slide-kinds",
                }
            )
            continue
        for where, text in _strings(sl.model_dump(exclude={"kind"}), f"/slides/{i}"):
            for var in PLACEHOLDER.findall(text):
                if var not in known:
                    errors.append(
                        {
                            "pointer": where,
                            "message": f"unknown placeholder ${{{var}}}; declare it as a parameter or use one of the variables",
                        }
                    )
        # config check with parameters stubbed out: required keys present, values of the right shape
        trial = _substitute(sl.config, {v: "x" for v in known}, strict=False)
        try:
            pres.normalise_slides([pres.SlideIn(kind=sl.kind, config=_loosen(sl.kind, trial))])
        except DomainError as exc:
            errors.append(
                {
                    "pointer": f"/slides/{i}" + (exc.pointer or "").split("/slides/0", 1)[-1],
                    "message": exc.message,
                }
            )
        except (
            ValueError,
            TypeError,
        ) as exc:  # a config value of the wrong shape (e.g. text where a number belongs)
            errors.append({"pointer": f"/slides/{i}/config", "message": str(exc)})
    for i, r in enumerate(body.post_rules):
        if not WHEN.match(r.when):
            errors.append(
                {
                    "pointer": f"/post_rules/{i}/when",
                    "message": "use `<scope.items|scope.projects|scope.plan_items> <op> <number>`",
                }
            )
        for k in r.kinds:
            if k not in pres.SLIDE_KINDS:
                errors.append({"pointer": f"/post_rules/{i}/kinds", "message": f"unknown slide kind {k!r}"})
    return (body if not errors else None), errors


def _loosen(kind: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """During validation a stubbed placeholder ('x') must not trip value checks that only make sense for the real value."""
    out = dict(cfg)
    for k, v in list(out.items()):
        if v == "x" and k in ("days", "limit"):
            out[k] = 14
        if v == "x" and k in ("dimA", "dimB"):
            out[k] = "team"
        if v == "x" and k == "measure":
            out[k] = "count"
        if v == "x" and k == "rows" and kind == "gantt":
            out[k] = "epic"
    return out


def _strings(x: Any, base: str) -> list[tuple[str, str]]:
    if isinstance(x, str):
        return [(base, x)]
    if isinstance(x, dict):
        return [r for k, v in x.items() for r in _strings(v, f"{base}/{k}")]
    if isinstance(x, list):
        return [r for i, v in enumerate(x) for r in _strings(v, f"{base}/{i}")]
    return []


def _substitute(x: Any, values: dict[str, Any], *, strict: bool = True) -> Any:
    """`${name}` → value. A string that is only one placeholder keeps the value's type (an int stays an int)."""
    if isinstance(x, str):
        whole = PLACEHOLDER.fullmatch(x)
        if whole:
            name = whole.group(1)
            if name in values:
                return values[name]
            return x if not strict else _missing(name)
        return PLACEHOLDER.sub(
            lambda mt: (
                str(values[mt.group(1)])
                if mt.group(1) in values
                else (mt.group(0) if not strict else _missing(mt.group(1)))
            ),
            x,
        )
    if isinstance(x, dict):
        return {k: _substitute(v, values, strict=strict) for k, v in x.items()}
    if isinstance(x, list):
        return [_substitute(v, values, strict=strict) for v in x]
    return x


def _missing(name: str) -> Any:
    raise Invalid("missing_variable", f"no value for ${{{name}}}", pointer=f"/params/{name}")


def resolve_params(body: TemplateBody, given: dict[str, Any]) -> dict[str, Any]:
    """Defaults applied, required checked, types coerced (`int`)."""
    out: dict[str, Any] = {}
    unknown = set(given) - {p.name for p in body.params}
    if unknown:
        raise Invalid(
            "unknown_param",
            f"unknown parameter(s) {sorted(unknown)}",
            pointer=f"/params/{sorted(unknown)[0]}",
        )
    for p in body.params:
        v = given.get(p.name, p.default)
        if v in (None, "") and p.required:
            raise Invalid("missing_param", f"parameter {p.name!r} is required", pointer=f"/params/{p.name}")
        if p.type == "int" and v not in (None, ""):
            try:
                v = int(v)
            except (TypeError, ValueError) as exc:
                raise Invalid(
                    "invalid_param", f"{p.name} must be a whole number", pointer=f"/params/{p.name}"
                ) from exc
        out[p.name] = v if v is not None else ""
    return out


_OPS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}


def instantiate(
    body: TemplateBody, params: dict[str, Any], variables: dict[str, Any], stats: dict[str, int]
) -> tuple[str, list[pres.SlideIn]]:
    """Copy the template into concrete slides. Nothing keeps a reference to the template afterwards (OM §14.2)."""
    resolved = resolve_params(body, params)
    for p in body.params:  # a project/product/team parameter left empty means "the scope I was started from"
        if (
            resolved.get(p.name) in (None, "")
            and p.type in ("project", "product", "team")
            and variables.get("scope.kind") == p.type
        ):
            resolved[p.name] = variables.get("scope.ref", "")
    values = {**variables, **resolved}
    dropped: set[str] = set()
    for r in body.post_rules:
        mt = WHEN.match(r.when)
        if mt and _OPS[mt.group(2)](stats.get(mt.group(1), 0), int(mt.group(3))):
            dropped.update(r.kinds)
    slides: list[pres.SlideIn] = []
    for sl in body.slides:
        if sl.kind in dropped:
            continue
        slides.append(
            pres.SlideIn(
                kind=sl.kind,
                title=_substitute(sl.title_template, values) if sl.title_template else None,
                subtitle=_substitute(sl.subtitle_template, values) if sl.subtitle_template else None,
                config=_substitute(sl.config, values),
                notes=_substitute(sl.notes_template, values) if sl.notes_template else None,
                layout=sl.layout,
            )
        )
    title = _substitute(body.name, values)
    return str(title), slides
