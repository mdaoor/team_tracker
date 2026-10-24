"""Validated filter AST. Leaves are `Clause(kind, args)`; composition is `And`/`Or`/`Not`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class FilterError(ValueError):
    """Invalid filter. `pointer` is a JSON pointer into the submitted document (RFC 6901)."""

    def __init__(self, pointer: str, message: str) -> None:
        super().__init__(f"{pointer or '/'}: {message}")
        self.pointer, self.message = pointer, message


@dataclass(frozen=True)
class Clause:
    kind: str
    args: dict[str, Any] = field(default_factory=dict)
    pointer: str = ""


@dataclass(frozen=True)
class And:
    items: tuple[Node, ...]


@dataclass(frozen=True)
class Or:
    items: tuple[Node, ...]


@dataclass(frozen=True)
class Not:
    item: Node


Node = Clause | And | Or | Not
