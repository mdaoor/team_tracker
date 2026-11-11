# ADR-0004: Optional `mode` on ClientBatch
Date: 2026-10-23   Status: accepted

## Context
Product requirement: two sending modes (assisted, manual). Managers want to know which mode yields cleaner data.

## Decision
Add `mode: "assisted" | "manual" | null` to `ClientBatch` (backwards compatible, defaults to null). The server stores
it in `Event.payload.mode` of the first event of each message. `tt_schema` keeps the original dataclass fields
intact; the parity test whitelists this documented addition.

## Alternatives considered
- Suffix on `client_version` ("0.1+manual"): string parsing, no schema validation.
