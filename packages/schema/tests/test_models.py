from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from tt_schema import ALL_MODELS, CAPTURE_FIELDS, new_id
from tt_schema.items import WorkItem
from tt_schema.testing.factories import FACTORIES, build
from tt_schema.testing.strategies import STRATEGIES


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_factory_builds_and_roundtrips(model: type) -> None:
    obj = FACTORIES[model]()
    assert model.model_validate_json(obj.model_dump_json()) == obj


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_extra_fields_forbidden(model: type) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        model.model_validate({**FACTORIES[model]().model_dump(), "bogus": 1})


def _hypothesis_test(model: type) -> None:
    @settings(max_examples=15, deadline=None)
    @given(STRATEGIES[model])
    def run(obj: object) -> None:
        assert model.model_validate_json(obj.model_dump_json()) == obj  # type: ignore[attr-defined]

    run()


@pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
def test_property_roundtrip(model: type) -> None:
    _hypothesis_test(model)


def test_decimal_serialises_as_string() -> None:
    item = build(WorkItem, weight=Decimal("2.5"))
    assert item.model_dump(mode="json")["weight"] == "2.5"


def test_layers_cover_every_work_item_field() -> None:
    base = {"id", "created_at", "updated_at", "created_by", "deleted_at"}
    for name, field in WorkItem.model_fields.items():
        if name in base:
            continue
        assert (field.json_schema_extra or {}).get("layer") in {"capture", "overlay", "system", "either"}, (
            name
        )  # type: ignore[union-attr]
    assert CAPTURE_FIELDS == {"title", "reporter_id", "actual_start", "actual_end"}


def test_ids_are_time_sortable_uuid7() -> None:
    a, b = new_id(), new_id()
    assert len(a) == 36 and a[14] == "7"
    assert a <= b or a[:13] == b[:13]
