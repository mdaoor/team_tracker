"""Presentations (ADR-0021): slide kinds, config validation, templates, and the per-slide data a deck needs."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from tt_schema.base import new_id

from tt_server.domain.errors import Invalid

SLIDE_KINDS = (
    "title", "kpis", "projects", "project_detail", "pipeline", "explorer", "radar", "gantt", "now", "people",
    "violations", "habits", "insight", "view", "chart", "table", "markdown", "image", "section_divider",
    "roadmap", "milestones", "deliverables", "plan_vs_actual", "phase_status", "project_overview",
)  # fmt: skip
PLAN_KINDS = ("roadmap", "milestones", "deliverables", "plan_vs_actual", "phase_status", "project_overview")
# The plan-only rule: what each kind needs, and what it does when it has nothing. Mirrored by KIND_META in apps/presentation
# (tests/unit/test_slide_kind_meta.py keeps the two identical).
KIND_META: dict[str, dict[str, str]] = {
    **{k: {"requires": "plan", "render_empty": "placeholder"} for k in (*PLAN_KINDS, "title", "section_divider", "markdown", "image")},
    **{k: {"requires": "both", "render_empty": "placeholder"} for k in ("projects", "project_detail", "gantt")},
    "insight": {"requires": "both", "render_empty": "note"},
    **{k: {"requires": "activity", "render_empty": "note"} for k in ("explorer", "radar", "kpis", "pipeline", "now", "people", "violations", "habits", "view", "chart", "table")},
}  # fmt: skip
SECTION_KINDS = {
    "kpis",
    "projects",
    "pipeline",
    "explorer",
    "radar",
    "gantt",
    "now",
    "people",
    "violations",
    "habits",
    "project_detail",
    "insight",
    *PLAN_KINDS,
}
QUERY_KINDS = {"view", "chart", "table"}

# kind -> (required config keys, defaults)
CONFIG: dict[str, tuple[tuple[str, ...], dict[str, Any]]] = {
    "title": ((), {}),
    "kpis": ((), {}),
    "projects": ((), {}),
    "project_detail": (("project_key",), {}),
    "pipeline": ((), {"process": None}),
    "explorer": ((), {"dimA": "team", "dimB": "stage", "measure": "count"}),
    "radar": ((), {"days": 14}),
    "gantt": ((), {"rows": "epic", "baseline": None}),
    "now": ((), {}),
    "people": ((), {}),
    "violations": ((), {"with_habits": False}),
    "habits": ((), {}),
    "insight": ((), {"scope": None}),
    "view": (("saved_view_id",), {"layout": None}),
    "chart": (("rows",), {"cols": None, "measure": "count", "filter": {}}),
    "table": ((), {"filter": {}, "sort": [{"field": "priority", "dir": "desc"}], "limit": 50}),
    "markdown": (("text",), {}),
    "image": ((), {"url": None, "attachment_id": None}),
    "section_divider": ((), {}),
    "roadmap": ((), {"project_key": None, "show_baseline": True}),
    "milestones": ((), {"project_key": None, "style": "table"}),
    "deliverables": ((), {"project_key": None, "phase": None}),
    "plan_vs_actual": ((), {"project_key": None}),
    "phase_status": ((), {"project_key": None}),
    "project_overview": ((), {"project_key": None}),
}
# roadmap slide config keys the renderer understands (optional; the renderer defaults to phase / fit)
ROADMAP_CHOICES: dict[str, tuple[str, ...]] = {
    "rows": ("phase", "epic", "milestone"),
    "zoom": ("day", "week", "month", "fit"),
}
EXPLORER_DIMS = {"assignee", "team", "project", "product", "stage", "priority", "epic", "tags"}
EXPLORER_MEASURES = {"count", "hours", "wt", "viol"}


class SlideIn(BaseModel):
    # Input shape of one slide in a presentation (kind, titles, config).

    id: str | None = None
    kind: str
    title: str | None = None
    subtitle: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    layout: Literal["full", "split", "grid"] = "full"
    live: bool = False  # query slides re-run when presented
    fit: Literal["paginate", "shrink"] = (
        "paginate"  # long content: continuation slides "(cont.)", or scale to fit one slide
    )

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in SLIDE_KINDS:
            raise ValueError(f"unknown slide kind {v!r}; one of {', '.join(SLIDE_KINDS)}")
        return v


IMAGE_URL = re.compile(r"^(data:image/(png|jpeg|gif|webp);|/(?!/))")


def normalise_slides(slides: list[SlideIn]) -> list[dict[str, Any]]:
    """Validate each slide's config for its kind, fill defaults, assign ids and positions."""
    out: list[dict[str, Any]] = []
    for pos, s in enumerate(slides):
        required, defaults = CONFIG[s.kind]
        cfg = {**defaults, **s.config}
        for k in required:
            if cfg.get(k) in (None, ""):
                raise Invalid(
                    "invalid_slide",
                    f"{s.kind} slides need config.{k}",
                    pointer=f"/slides/{pos}/config/{k}",
                )
        if s.kind == "explorer":
            if cfg["dimA"] not in EXPLORER_DIMS or (cfg["dimB"] and cfg["dimB"] not in EXPLORER_DIMS):
                raise Invalid(
                    "invalid_slide",
                    f"dimensions must be among {sorted(EXPLORER_DIMS)}",
                    pointer=f"/slides/{pos}/config",
                )
            if cfg["measure"] not in EXPLORER_MEASURES:
                raise Invalid(
                    "invalid_slide",
                    f"measure must be one of {sorted(EXPLORER_MEASURES)}",
                    pointer=f"/slides/{pos}/config/measure",
                )
        if s.kind == "image" and cfg.get("url") and not IMAGE_URL.match(str(cfg["url"])):
            raise Invalid(
                "invalid_slide",
                "image.url must be a data:image/... URL or a same-origin path (no external hosts)",
                pointer=f"/slides/{pos}/config/url",
            )
        if s.kind == "radar" and not (1 <= int(cfg["days"]) <= 120):
            raise Invalid("invalid_slide", "radar days must be 1..120", pointer=f"/slides/{pos}/config/days")
        if s.kind == "gantt" and cfg["rows"] not in {"epic", "item", "milestone"}:
            raise Invalid(
                "invalid_slide",
                "gantt rows must be epic, item or milestone",
                pointer=f"/slides/{pos}/config/rows",
            )
        if s.kind == "roadmap":
            for key, allowed in ROADMAP_CHOICES.items():
                if key in cfg and cfg[key] not in allowed:
                    raise Invalid(
                        "invalid_slide",
                        f"roadmap {key} must be one of {', '.join(allowed)}",
                        pointer=f"/slides/{pos}/config/{key}",
                    )
        out.append(
            {
                "id": s.id or new_id(), "kind": s.kind, "title": s.title, "subtitle": s.subtitle, "config": cfg,
                "notes": s.notes, "layout": s.layout, "live": s.live, "fit": s.fit, "position": pos,
            }
        )  # fmt: skip
    return out


def parse_scope_ref(scope: str) -> str:
    """Validate the `kind:ref` shape; the assembler resolves the reference and answers 404 for unknown ones."""
    kind = scope.partition(":")[0]
    if kind not in {"workspace", "product", "project", "team", "person"}:
        raise Invalid("invalid_scope", f"unknown scope {scope!r}", pointer="/scope")
    return scope
