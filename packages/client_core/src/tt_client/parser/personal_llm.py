"""Optional gateway assist for the personal quick-add (Phase 15 §15.2): classification only, with the deterministic parser always first.

The gateway answers `POST /v1/translate/personal` with refined `kind`/`category`/`project_hint` for lines the parser could not place.
The Team Tracker server token is never sent (the gateway has its own caller auth), and the call is made only when the person
turned `privacy.assist` on (ADR-0053). Only those three fields are taken from the answer; every number and date stays what the local parser produced. Any failure (no gateway,
slow, no local model, bad JSON) returns the parser's drafts unchanged: personal capture never waits on a model.
"""

from __future__ import annotations

from typing import Any

import httpx

from tt_client.personal.model import KIND_CATEGORY, PRIVACY_RANK, Category, Draft, Kind

DEFAULT_TIMEOUT = 1.5


def fetch(
    gateway_url: str,
    line: str,
    *,
    actor: str,
    now: str,
    time_zone: str,
    projects: list[dict[str, Any]],
    timeout: float = DEFAULT_TIMEOUT,
    verify: Any = True,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]] | None:
    body = {"actor": actor, "line": line, "now": now, "time_zone": time_zone, "pack": {"projects": projects}}
    try:
        http = client or httpx.Client(timeout=timeout, verify=verify)
        r = http.post(f"{gateway_url.rstrip('/')}/v1/translate/personal", json=body)
        if r.status_code != 200:
            return None
        drafts = r.json()["output"]["drafts"]
        return [d for d in drafts if isinstance(d, dict)]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def refine(
    d: Draft,
    kind: Kind | None,
    category: Category | None,
    hint: str | None = None,
    project_keys: set[str] | None = None,
) -> tuple[Draft, list[str]]:
    """Fold a model's reading of one draft into the parser's. The model can make a line *more private* on its own; anything less private
    (personal -> development/work) is stored as a suggestion for the person to confirm, never applied (a wrong or injected answer must not
    turn a private line into something that can be offered to the server). Numbers are never touched."""
    notes: list[str] = []
    upd: dict[str, Any] = {}
    final = d.category
    if d.kind_source == "default" and kind is not None:
        new_cat = (category or KIND_CATEGORY[kind]) if d.category_source == "kind" else d.category
        if PRIVACY_RANK[new_cat] >= PRIVACY_RANK[d.category]:
            upd.update(kind=kind, category=new_cat, kind_source="label")
            final = new_cat
        else:
            upd.update(suggested_kind=kind, suggested_category=new_cat)
            notes.append(f"{d.title!r}: looks like {new_cat.value}; asking before treating it that way")
    elif (
        d.category_source == "kind"
        and category is not None
        and PRIVACY_RANK[category] > PRIVACY_RANK[d.category]
    ):
        upd["category"] = final = category
        notes.append(f"{d.title!r}: kept private (read as {category.value})")
    if (
        hint
        and d.project_hint is None
        and final != Category.PERSONAL
        and (project_keys is None or hint.upper() in project_keys)
    ):
        upd["project_hint"] = hint.upper()
    return d.model_copy(update=upd), notes


def apply_assist(drafts: list[Draft], refined: list[dict[str, Any]] | None) -> list[Draft]:
    """Take `kind`, `category` and `project_hint` from the gateway through `refine`; never the numbers."""
    if not refined or len(refined) != len(drafts):
        return drafts
    out: list[Draft] = []
    for d, r in zip(drafts, refined, strict=True):
        try:
            kind, category = Kind(r["kind"]), Category(r["category"])
        except (KeyError, ValueError):
            out.append(d)
            continue
        out.append(refine(d, kind, category, r.get("project_hint"))[0])
    return out
