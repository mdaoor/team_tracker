"""Small parsers for query parameters that name enum members."""

from __future__ import annotations

from enum import Enum

from tt_server.api.errors import ApiError


def enum_param[E: Enum](cls: type[E], value: str, name: str) -> E:
    try:
        return cls(value)
    except ValueError as exc:
        raise ApiError(
            400, f"invalid_{name}", f"{name} must be one of {[e.value for e in cls]}", pointer=f"/{name}"
        ) from exc
