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


# ---------------------------------------------------------------- Phase 15 additions (ADR-0054): source and corrects
def test_phase_15_fields_are_optional_so_old_clients_still_validate() -> None:
    old = {
        "actor": "@x",
        "action": "log_time",
        "at": "2026-10-23T09:00:00+03:00",
        "client_id": "c",
        "minutes": 5,
    }
    m = ClientMessage.model_validate(old)
    assert m.source is None and m.corrects is None


def test_calendar_ref_and_source_round_trip() -> None:
    m = ClientMessage.model_validate(
        {
            "actor": "@x",
            "action": "log_time",
            "at": "2026-10-26T10:00:00+03:00",
            "client_id": "c",
            "minutes": 60,
            "source": {
                "origin": "calendar:microsoft",
                "calendar_ref": {
                    "provider": "microsoft",
                    "event_id": "AAMk1",
                    "series_id": "AAMk0",
                    "instance_start": "2026-10-26T09:00:00+03:00",
                },
            },
        }
    )
    assert m.source and m.source.calendar_ref and m.source.calendar_ref.provider == "microsoft"
    assert ClientMessage.model_validate(json.loads(m.model_dump_json())) == m


@pytest.mark.parametrize(
    "source",
    [
        {"origin": "calendar:caldav"},  # not a supported origin
        {"origin": "personal", "calendar_ref": {"provider": "ical", "event_id": "x"}},  # unknown provider
        {"origin": "personal", "calendar_ref": {"provider": "google"}},  # event_id is required
        {"origin": "personal", "titel": "typo"},  # extra fields are refused
    ],
)
def test_bad_sources_are_rejected(source: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ClientMessage.model_validate(
            {
                "actor": "@x",
                "action": "log_time",
                "at": "2026-10-26T10:00:00+03:00",
                "client_id": "c",
                "source": source,
            }
        )


def test_the_generated_json_schema_lists_the_new_fields_without_refs() -> None:
    schema = client_message_json_schema()
    props = schema["properties"]
    assert isinstance(props, dict) and {"source", "corrects"} <= set(props)
    assert "$ref" not in json.dumps(schema)
    assert "calendar:microsoft" in json.dumps(props["source"])


def test_schema_minor_is_declared_and_the_default_stays_backwards_compatible() -> None:
    from tt_schema import CLIENT_SCHEMA_MINOR

    assert CLIENT_SCHEMA_MINOR == "0.2" and ClientBatch(workspace="w", messages=[]).client_version == "0.1"
