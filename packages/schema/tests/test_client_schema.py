from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from tt_schema import ClientAction, ClientBatch, ClientMessage, client_message_json_schema

# The three sample lines of UX §2.2 / §7.1, hand-written as the JSON a translator would emit.
SAMPLES = [
    {
        "actor": "@mahmoud",
        "action": "create",
        "at": "2026-10-23T09:00:00+03:00",
        "client_id": "c-1",
        "note": "fix the login redirect loop",
        "minutes": 120,
        "planned_end": "2026-10-24T17:00:00+03:00",
        "raw_input": "fix the login redirect loop, took 2h, plan to finish tomorrow",
    },
    {
        "actor": "@sara",
        "action": "start",
        "at": "2026-10-23T09:05:00+03:00",
        "client_id": "c-2",
        "item": {"key": "WEB-101"},
        "project": "WEB",
        "tags": ["auth"],
    },
    {
        "actor": "@ali",
        "action": "log_time",
        "at": "2026-10-23T09:10:00+03:00",
        "client_id": "c-3",
        "item": {"title": "password reset email template"},
        "minutes": 45,
        "estimate": {"value": 3, "unit": "hours"},
    },
]


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = client_message_json_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("sample", SAMPLES)
def test_samples_validate(validator: Draft202012Validator, sample: dict[str, object]) -> None:
    validator.validate(sample)
    ClientMessage.model_validate(sample)


def test_unknown_action_rejected(validator: Draft202012Validator) -> None:
    bad = {**SAMPLES[0], "action": "teleport"}
    assert not validator.is_valid(bad)
    with pytest.raises(ValidationError):
        ClientMessage.model_validate(bad)


def test_schema_has_no_refs_and_inline_enums() -> None:
    text = json.dumps(client_message_json_schema())
    assert "$ref" not in text and "$defs" not in text
    assert set(ClientAction) == {
        ClientAction(v) for v in client_message_json_schema()["properties"]["action"]["enum"]
    }  # type: ignore[index]


def test_batch_schema() -> None:
    schema = client_message_json_schema(batch=True)
    Draft202012Validator(schema).validate({"workspace": "acme", "messages": SAMPLES})
    batch = ClientBatch.model_validate({"workspace": "acme", "messages": SAMPLES})
    assert batch.client_version == "0.1" and len(batch.messages) == 3
    assert isinstance(batch.messages[0].at, datetime) and batch.messages[0].at.tzinfo is not None
    assert batch.messages[0].at.astimezone(UTC).hour == 6
