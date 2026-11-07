"""factory_boy factories for every model in ALL_MODELS (required fields get deterministic-ish values)."""

from __future__ import annotations

import enum
import types
import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import factory
from pydantic import BaseModel

from tt_schema.base import new_id
from tt_schema.registry import ALL_MODELS


def _value_for(tp: Any, name: str) -> Any:
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        return _value_for(next(a for a in args if a is not type(None)), name)
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return next(iter(tp))
    if tp is str:
        return new_id() if name.endswith("_id") or name.endswith("_by") else f"{name}-{new_id()[-8:]}"
    if tp is bool:
        return False
    if tp is int:
        return 1
    if tp is Decimal:
        return Decimal("1.0")
    if tp is datetime:
        return datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    if tp is date:
        return date(2026, 10, 23)
    if origin is list:
        return []
    if origin is dict:
        return {}
    raise TypeError(f"no factory value for {name}: {tp!r}")


def make_factory(model: type[BaseModel]) -> type[factory.Factory]:
    decls: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if field.is_required():
            decls[name] = factory.LazyAttribute(lambda _o, n=name, t=field.annotation: _value_for(t, n))
    meta = type("Meta", (), {"model": model})
    return typing.cast(
        type[factory.Factory], type(f"{model.__name__}Factory", (factory.Factory,), {"Meta": meta, **decls})
    )


FACTORIES: dict[type[BaseModel], type[factory.Factory]] = {m: make_factory(m) for m in ALL_MODELS}


def build(model: type[BaseModel], **overrides: Any) -> Any:
    return FACTORIES[model](**overrides)
