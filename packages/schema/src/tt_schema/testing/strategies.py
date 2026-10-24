"""Hypothesis strategies derived from the model annotations (required fields only; defaults fill the rest)."""

from __future__ import annotations

import enum
import string
import types
import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from hypothesis import strategies as st
from pydantic import BaseModel

from tt_schema.client import ClientBatch, ClientMessage, ClientTaskRef
from tt_schema.registry import ALL_MODELS

_TEXT = st.text(alphabet=string.ascii_letters + string.digits + " _-", min_size=1, max_size=24)


def strategy_for_annotation(tp: Any) -> st.SearchStrategy[Any]:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        return st.one_of(*(strategy_for_annotation(a) for a in args))
    if tp is type(None):
        return st.none()
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return st.sampled_from(list(tp))
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return model_strategy(tp)
    if tp is str:
        return _TEXT
    if tp is bool:
        return st.booleans()
    if tp is int:
        return st.integers(min_value=0, max_value=10_000)
    if tp is float:
        return st.floats(min_value=0, max_value=1, allow_nan=False)
    if tp is Decimal:
        return st.decimals(min_value=0, max_value=1000, places=2)
    if tp is datetime:
        return st.datetimes(
            min_value=datetime(2000, 1, 1), max_value=datetime(2100, 1, 1), timezones=st.just(UTC)
        )
    if tp is date:
        return st.dates()
    if origin is list:
        return st.lists(strategy_for_annotation(args[0]) if args else _TEXT, max_size=3)
    if origin is dict:
        return st.dictionaries(_TEXT, st.integers(0, 100) | _TEXT, max_size=3)
    return st.just(None) if tp is Any else st.none()


def model_strategy(model: type[BaseModel]) -> st.SearchStrategy[Any]:
    required = {
        n: strategy_for_annotation(f.annotation) for n, f in model.model_fields.items() if f.is_required()
    }
    return st.builds(model, **required)


STRATEGIES: dict[type[BaseModel], st.SearchStrategy[Any]] = {
    m: model_strategy(m) for m in (*ALL_MODELS, ClientTaskRef, ClientMessage, ClientBatch)
}
