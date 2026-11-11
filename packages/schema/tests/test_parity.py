"""Schema parity: the Pydantic port must match docs/spec/team_tracker_models.py exactly."""

from __future__ import annotations

import dataclasses
import enum
import importlib.util
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import tt_schema.enums as enums
from tt_schema import ALL_MODELS
from tt_schema.client import ClientAction, ClientBatch, ClientMessage, ClientTaskRef

# Fields added on top of the spec by an ADR (backwards compatible, always optional).
DOCUMENTED_ADDITIONS: dict[str, set[str]] = {
    "ClientBatch": {"mode", "commands"},  # ADR-0004 (mode), ADR-0051 (commands)
    "ClientMessage": {"corrects", "source"},  # ADR-0054 (Phase 15)
}

SPEC = Path(__file__).resolve().parents[3] / "docs" / "spec" / "team_tracker_models.py"


@pytest.fixture(scope="module")
def spec() -> Any:
    loader_spec = importlib.util.spec_from_file_location("spec_models", SPEC)
    assert loader_spec and loader_spec.loader
    module = importlib.util.module_from_spec(loader_spec)
    import sys

    sys.modules["spec_models"] = module  # dataclasses needs the module registered
    loader_spec.loader.exec_module(module)
    return module


def _spec_classes(spec: Any) -> list[type]:
    return [c for c in vars(spec).values() if dataclasses.is_dataclass(c) and c.__module__ == "spec_models"]


def test_model_count_matches_source(spec: Any) -> None:
    assert [m.__name__ for m in ALL_MODELS] == [m.__name__ for m in spec.ALL_MODELS]
    assert len(ALL_MODELS) == len(spec.ALL_MODELS)


@pytest.mark.parametrize(
    "name", [m.__name__ for m in (*ALL_MODELS, ClientTaskRef, ClientMessage, ClientBatch)]
)
def test_fields_match(spec: Any, name: str) -> None:
    pyd = {m.__name__: m for m in (*ALL_MODELS, ClientTaskRef, ClientMessage, ClientBatch)}[name]
    dc = getattr(spec, name)
    dc_fields = dataclasses.fields(dc)
    expected_names = [f.name for f in dc_fields] + sorted(DOCUMENTED_ADDITIONS.get(name, set()))
    assert list(pyd.model_fields) == expected_names, "field names/order differ"
    dc_required = {
        f.name
        for f in dc_fields
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    pyd_required = {n for n, f in pyd.model_fields.items() if f.is_required()}
    assert pyd_required == dc_required
    for f in dc_fields:
        pf = pyd.model_fields[f.name]
        if f.default is not dataclasses.MISSING:
            assert pf.default == f.default or (
                isinstance(f.default, enum.Enum) and pf.default.value == f.default.value
            )
        elif f.default_factory is not dataclasses.MISSING and f.name not in {
            "id",
            "created_at",
            "updated_at",
            "joined_at",
        }:
            assert pf.default_factory is not None, f.name
            expected, actual = f.default_factory(), pf.default_factory()  # type: ignore[call-arg]
            norm = lambda v: [getattr(x, "value", x) for x in v] if isinstance(v, list) else v  # noqa: E731
            if not isinstance(expected, datetime):
                assert norm(actual) == norm(expected), f.name


def test_enums_match(spec: Any) -> None:
    spec_enums = {
        n: c
        for n, c in vars(spec).items()
        if isinstance(c, type) and issubclass(c, enum.Enum) and c is not spec.StrEnum
    }
    assert spec_enums, "no enums found in spec"
    ported = {
        **{n: getattr(enums, n) for n in dir(enums) if isinstance(getattr(enums, n), type)},
        "ClientAction": ClientAction,
    }
    for name, cls in spec_enums.items():
        assert name in ported, f"enum {name} not ported"
        assert [(m.name, m.value) for m in ported[name]] == [(m.name, m.value) for m in cls], name
