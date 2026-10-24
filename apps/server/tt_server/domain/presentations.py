"""Presentations (ADR-0021): slide kinds, config validation, templates, and the per-slide data a deck needs."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from tt_schema.base import new_id

from tt_server.api.errors import ApiError

SLIDE_KINDS = (
    "title", "kpis", "projects", "project_detail", "pipeline", "explorer", "radar", "gantt", "now", "people",
    "violations", "habits", "insight", "view", "chart", "table", "markdown", "image", "section_divider",
)  # fmt: skip
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
}
EXPLORER_DIMS = {"assignee", "team", "project", "product", "stage", "priority", "epic", "tags"}
EXPLORER_MEASURES = {"count", "hours", "wt", "viol"}


class SlideIn(BaseModel):
    id: str | None = None
    kind: str
    title: str | None = None
    subtitle: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    layout: Literal["full", "split", "grid"] = "full"
    live: bool = False  # query slides re-run when presented

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in SLIDE_KINDS:
            raise ValueError(f"unknown slide kind {v!r}; one of {', '.join(SLIDE_KINDS)}")
        return v


def normalise_slides(slides: list[SlideIn]) -> list[dict[str, Any]]:
    """Validate each slide's config for its kind, fill defaults, assign ids and positions."""
    out: list[dict[str, Any]] = []
    for pos, s in enumerate(slides):
        required, defaults = CONFIG[s.kind]
        cfg = {**defaults, **s.config}
        for k in required:
            if cfg.get(k) in (None, ""):
                raise ApiError(
                    400,
                    "invalid_slide",
                    f"{s.kind} slides need config.{k}",
                    pointer=f"/slides/{pos}/config/{k}",
                )
        if s.kind == "explorer":
            if cfg["dimA"] not in EXPLORER_DIMS or (cfg["dimB"] and cfg["dimB"] not in EXPLORER_DIMS):
                raise ApiError(
                    400,
                    "invalid_slide",
                    f"dimensions must be among {sorted(EXPLORER_DIMS)}",
                    pointer=f"/slides/{pos}/config",
                )
            if cfg["measure"] not in EXPLORER_MEASURES:
                raise ApiError(
                    400,
                    "invalid_slide",
                    f"measure must be one of {sorted(EXPLORER_MEASURES)}",
                    pointer=f"/slides/{pos}/config/measure",
                )
        if s.kind == "radar" and not (1 <= int(cfg["days"]) <= 120):
            raise ApiError(
                400, "invalid_slide", "radar days must be 1..120", pointer=f"/slides/{pos}/config/days"
            )
        if s.kind == "gantt" and cfg["rows"] not in {"epic", "item", "milestone"}:
            raise ApiError(
                400,
                "invalid_slide",
                "gantt rows must be epic, item or milestone",
                pointer=f"/slides/{pos}/config/rows",
            )
        out.append(
            {
                "id": s.id or new_id(), "kind": s.kind, "title": s.title, "subtitle": s.subtitle, "config": cfg,
                "notes": s.notes, "layout": s.layout, "live": s.live, "position": pos,
            }
        )  # fmt: skip
    return out


def _s(kind: str, title: str | None = None, **config: Any) -> SlideIn:
    return SlideIn(kind=kind, title=title, config=config)


def _portfolio() -> list[SlideIn]:
    return [
        _s("kpis", "Key numbers"), _s("projects", "Projects"), _s("pipeline", "Pipeline"),
        _s("explorer", "Explorer", dimA="team", dimB="stage", measure="count"), _s("radar", "Next 14 days", days=14),
        _s("gantt", "Plan", rows="epic"), _s("now", "Working now"), _s("people", "People"), _s("violations", "Violations & habits", with_habits=True),
    ]  # fmt: skip


TEMPLATES: dict[str, tuple[str, list[SlideIn]]] = {
    "portfolio_review": ("Portfolio review", _portfolio()),
    "sprint_review": (
        "Sprint review",
        [
            _s("title", "Sprint review"),
            _s("kpis", "Delivery"),
            _s("pipeline", "Flow"),
            _s("explorer", "Done by person", dimA="assignee", measure="wt"),
            _s("radar", "Coming up", days=7),
            _s("insight", "Summary"),
        ],
    ),  # fmt: skip
    "risk_review": (
        "Risk review",
        [
            _s("title", "Risk review"),
            _s("projects", "Projects at risk"),
            _s("explorer", "Risk exposure by project", dimA="project", measure="count"),
            _s("violations", "Open violations"),
            _s("radar", "Due soon", days=14),
        ],
    ),  # fmt: skip
    "team_1on1": (
        "Team 1:1",
        [
            _s("title", "1:1"),
            _s("people", "Workload"),
            _s("habits", "Habits"),
            _s("radar", "Next two weeks", days=14),
            _s("now", "Active now"),
        ],
    ),
    "exec_summary": (
        "Exec summary",
        [
            _s("title", "Executive summary"),
            _s("kpis", "Key numbers"),
            _s("insight", "Where we are"),
            _s("gantt", "Timeline", rows="epic"),
        ],
    ),
}


def template_slides(key: str) -> tuple[str, list[dict[str, Any]]]:
    if key not in TEMPLATES:
        raise ApiError(404, "not_found", f"unknown template {key!r}; one of {', '.join(TEMPLATES)}")
    title, slides = TEMPLATES[key]
    return title, normalise_slides([s.model_copy(deep=True) for s in slides])


def parse_scope_ref(scope: str) -> str:
    """Validate the `kind:ref` shape; the assembler resolves the reference and answers 404 for unknown ones."""
    kind = scope.partition(":")[0]
    if kind not in {"workspace", "product", "project", "team", "person"}:
        raise ApiError(400, "invalid_scope", f"unknown scope {scope!r}", pointer="/scope")
    return scope
