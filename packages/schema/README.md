# tt_schema

The single source of field names, enum values and defaults. Pydantic v2 port of
`docs/spec/team_tracker_models.py` (normative; do not rename or add synonyms).

| module | contents |
|---|---|
| `enums` | every enum, values identical to the spec |
| `tenancy`, `dimensions`, `process`, `products`, `projects`, `items`, `capture`, `governance`, `views`, `acl` | one module per spec section |
| `client` | `ClientAction`, `ClientTaskRef`, `ClientMessage`, `ClientBatch`, `client_message_json_schema()` |
| `registry` | `ALL_MODELS`, in the order of the source file |
| `testing` | factory_boy factories and Hypothesis strategies for every model (tests only) |

## Rules
- Models use `extra="forbid"`, `from_attributes=True`; `Decimal` serialises to a **string** in JSON.
- `WorkItem` fields carry `json_schema_extra={"layer": ...}` per object model §7:
  `capture` (written only by the capture layer), `overlay` (managers), `system`, `either`.
  `CAPTURE_FIELDS` is the frozenset of `capture` fields; overlay repositories must never UPDATE them.
- `tests/test_parity.py` imports the original dataclasses and asserts identical field names/order, required set,
  defaults and enum members. Change the spec through an ADR, then change both.

## Regenerating JSON Schema
```python
from tt_schema import client_message_json_schema

client_message_json_schema()  # ClientMessage, Draft 2020-12, enums inline, no $ref (Ollama `format`)
client_message_json_schema(batch=True)  # ClientBatch
```
The server serves both at `GET /api/v1/meta/schema/client-message[?batch=true]`.
