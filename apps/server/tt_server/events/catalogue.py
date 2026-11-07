"""Event catalogue: the allow-list of `Event.event_type` values, loaded from docs/api/event-catalogue.md."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


@dataclass(frozen=True)
class EventSpec:
    pattern: str  # "item.created" or "dimension.*"

    @property
    def is_wildcard(self) -> bool:
        return self.pattern.endswith(".*")

    def matches(self, event_type: str) -> bool:
        if self.is_wildcard:
            return event_type.startswith(self.pattern[:-1]) and len(event_type) > len(self.pattern) - 1
        return event_type == self.pattern


class UnknownEventTypeError(ValueError):
    pass


def _catalogue_path() -> Path:
    override = os.environ.get("TT_EVENT_CATALOGUE")
    if override:
        return Path(override)
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "docs" / "api" / "event-catalogue.md"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("docs/api/event-catalogue.md not found (set TT_EVENT_CATALOGUE)")


@lru_cache
def load_event_types(path: Path | None = None) -> dict[str, EventSpec]:
    text = (path or _catalogue_path()).read_text(encoding="utf-8")
    _, front, _ = text.split("---", 2)
    data = yaml.safe_load(front)
    return {p: EventSpec(p) for p in data["event_types"]}


EVENT_TYPES: dict[str, EventSpec] = load_event_types()


def is_known(event_type: str) -> bool:
    if event_type in EVENT_TYPES:
        return True
    return any(spec.matches(event_type) for spec in EVENT_TYPES.values() if spec.is_wildcard)


def require_known(event_type: str) -> None:
    if not is_known(event_type):
        raise UnknownEventTypeError(f"event type {event_type!r} is not in docs/api/event-catalogue.md")
