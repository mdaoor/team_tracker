"""JSON Schema (draft 2020-12) for the grammar, served to the UI so chips validate before they are sent."""

from __future__ import annotations

from typing import Any

from tt_filter.parse import DATE_KEYS, MAX_DEPTH, PRIORITIES, SEVERITIES, STATE_CATEGORIES

_STR = {"type": "string", "minLength": 1}
_STRS = {"oneOf": [_STR, {"type": "array", "items": _STR, "minItems": 1}]}
_DATE = {
    "type": "string",
    "description": "ISO date or token: today, tomorrow, start_of_week, end_of_month, -14d, +7d",
}
_DATES = {
    "type": "object",
    "minProperties": 1,
    "maxProperties": 1,
    "properties": {
        "between": {"type": "array", "items": _DATE, "minItems": 2, "maxItems": 2},
        "before": _DATE,
        "after": _DATE,
        "is_null": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def _CMP(extra: dict[str, Any]) -> dict[str, Any]:  # noqa: N802
    return {
        "type": "object",
        "minProperties": 1,
        "properties": {"gte": extra, "lte": extra, "eq": extra, "in": {"type": "array", "items": extra}},
        "additionalProperties": False,
    }


def _schema() -> dict[str, Any]:
    props: dict[str, Any] = {
        "dims": {
            "type": "object",
            "additionalProperties": {
                "oneOf": [
                    _STR,
                    {"type": "array", "items": _STR},
                    {
                        "type": "object",
                        "properties": {
                            "in": _STRS,
                            "not_in": _STRS,
                            "is_null": {"type": "boolean"},
                            "under": _STR,
                        },
                        "additionalProperties": False,
                        "minProperties": 1,
                        "maxProperties": 1,
                    },
                ]
            },
        },
        "people": {
            "type": "object",
            "properties": {
                "assignee": _STRS,
                "assignee_in_team": _STR,
                "reporter": _STRS,
                "unassigned": {"type": "boolean"},
                "assignment_role": {
                    "type": "object",
                    "properties": {"role": _STR, "person": _STR},
                    "required": ["role", "person"],
                },
            },
            "additionalProperties": False,
        },
        "people2": {
            "type": "object",
            "properties": {"reports_to": _STR, "kind": _STRS},
            "required": ["reports_to"],
            "additionalProperties": False,
        },
        "priority": _CMP({"enum": list(PRIORITIES)}),
        "weight": {
            **_CMP({"type": "number"}),
            "properties": {
                "gte": {"type": "number"},
                "lte": {"type": "number"},
                "eq": {"type": "number"},
                "scheme": _STR,
            },
        },
        "impact": _CMP({"type": "integer", "minimum": 1, "maximum": 5}),
        "touched": {
            "type": "object",
            "properties": {"since": _DATE},
            "required": ["since"],
            "additionalProperties": False,
        },
        "state": {
            "type": "object",
            "properties": {
                "category": {"type": "array", "items": {"enum": list(STATE_CATEGORIES)}},
                "in": _STRS,
            },
            "additionalProperties": False,
        },
        "stage": {
            "type": "object",
            "properties": {"in": _STRS, "process": _STR, "age_gt_expected": {"type": "boolean"}},
            "additionalProperties": False,
        },
        "type": {
            "type": "object",
            "properties": {"in": _STRS},
            "required": ["in"],
            "additionalProperties": False,
        },
        "tags": {
            "type": "object",
            "properties": {"all": _STRS, "any": _STRS, "none": _STRS},
            "additionalProperties": False,
        },
        "risk": {
            "type": "object",
            "properties": {"open_score_gte": {"type": "number"}, "linked": _STR},
            "additionalProperties": False,
        },
        "violations": {
            "type": "object",
            "properties": {
                "open": {"type": "boolean"},
                "category": _STRS,
                "severity_gte": {"enum": list(SEVERITIES)},
            },
            "additionalProperties": False,
        },
        "text": _STR,
        "project": {
            "type": "object",
            "properties": {"in": _STRS},
            "required": ["in"],
            "additionalProperties": False,
        },
        "include_inbox": {"type": "boolean"},
        **{k: _DATES for k in DATE_KEYS},
        "and": {"type": "array", "items": {"$ref": "#"}, "minItems": 1},
        "or": {"type": "array", "items": {"$ref": "#"}, "minItems": 1},
        "not": {"$ref": "#"},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://teamtracker.local/schemas/filter.json",
        "title": "Team Tracker filter",
        "description": f"Top-level keys are AND-ed. and/or/not nest up to {MAX_DEPTH} levels (enforced by the parser).",
        "type": "object",
        "properties": props,
        "additionalProperties": False,
    }


FILTER_SCHEMA: dict[str, Any] = _schema()
