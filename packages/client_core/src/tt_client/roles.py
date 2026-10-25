"""Role modes: a lens over the catalogue (which verbs surface first), never a permission.

`GET /me` says which modes the person may offer (`role_modes`); the chosen one is kept in the client config. The server
authorises every command with the person's real roles whatever the lens says.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from tt_client.catalogue import Catalogue, OpMeta

ALL_MODES = ("reporter", "manager", "scrum_master", "coach", "pm", "admin")
DEFAULT_MODE = "reporter"


@lru_cache
def _spec() -> dict[str, Any]:
    return dict(yaml.safe_load((Path(__file__).parent / "roles.yaml").read_text(encoding="utf-8")))


def normalise(name: str) -> str:
    """`scrum` -> `scrum_master`; raises ValueError for an unknown mode."""
    key = name.strip().lower().replace(" ", "_")
    key = _spec().get("aliases", {}).get(key, key)
    if key not in _spec()["modes"]:
        raise ValueError(f"unknown role mode {name!r} (known: {', '.join(ALL_MODES)})")
    return str(key)


def label(mode: str) -> str:
    return str(_spec()["modes"][normalise(mode)]["label"])


def describe(mode: str) -> str:
    return str(_spec()["modes"][normalise(mode)].get("description", ""))


def usable(allowed: list[str] | None) -> list[str]:
    """Modes to offer: what `/me` allows, always including reporter; offline default is reporter only."""
    base = [DEFAULT_MODE]
    for m in allowed or []:
        try:
            n = normalise(m)
        except ValueError:
            continue
        if n not in base:
            base.append(n)
    return [m for m in ALL_MODES if m in base]


def resolve_mode(wanted: str, allowed: list[str] | None) -> str:
    """The mode to use: `wanted` if allowed, else a ValueError telling which modes are."""
    mode = normalise(wanted)
    offered = usable(allowed)
    if mode not in offered:
        raise ValueError(f"role mode {mode!r} is not available to you (available: {', '.join(offered)})")
    return mode


@dataclass
class Chip:
    label: str
    op: str
    text: str

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "op": self.op, "text": self.text}


@dataclass
class Surface:
    mode: str
    first: list[OpMeta] = field(default_factory=list)  # pinned, ordered
    more: list[OpMeta] = field(default_factory=list)  # visible after the pinned ones
    hidden: list[OpMeta] = field(default_factory=list)  # reachable only via --all / tt cmd
    chips: list[Chip] = field(default_factory=list)

    @property
    def visible(self) -> list[OpMeta]:
        return [*self.first, *self.more]

    @property
    def reachable(self) -> list[OpMeta]:
        return [*self.first, *self.more, *self.hidden]


def surface(mode: str, catalogue: Catalogue) -> Surface:
    spec = _spec()["modes"][normalise(mode)]
    hidden_groups = set(spec.get("hidden_groups", []))
    hidden_perms = set(spec.get("hidden_perms", []))
    out = Surface(mode=normalise(mode))
    seen: set[str] = set()

    def is_hidden(o: OpMeta) -> bool:
        return o.group in hidden_groups or o.permission in hidden_perms

    for name in spec.get("first", []):
        o = catalogue.get(name)
        if o is not None and not is_hidden(o) and name not in seen:
            out.first.append(o)
            seen.add(name)
    group_rank = {g: i for i, g in enumerate(spec.get("groups", []))}
    rest = [o for o in catalogue if o.op not in seen]
    visible = sorted(
        (o for o in rest if not is_hidden(o) and (o.group in group_rank or not group_rank)),
        key=lambda o: group_rank.get(o.group, 99),
    )
    out.more = visible
    visible_names = {o.op for o in visible}
    out.hidden = [o for o in rest if o.op not in visible_names]
    out.chips = [
        Chip(c["label"], c["op"], c.get("text", ""))
        for c in spec.get("chips", [])
        if catalogue.get(c["op"]) is not None and not is_hidden(catalogue.require(c["op"]))
    ]
    return out


# -- cached /me answer: lets `tt role` list the right modes offline
class RoleCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, Any]:
        try:
            return dict(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            return {}

    def allowed(self) -> list[str]:
        return usable(self.load().get("role_modes"))

    def save(self, me: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"role_modes": me.get("role_modes", []), "roles": me.get("roles", [])})
        )

    def refresh(self, api: Any) -> list[str]:
        from tt_client.api import ApiError, Unreachable

        try:
            self.save(api.me())
        except (Unreachable, ApiError):
            pass  # offline: serve the last answer
        return self.allowed()
