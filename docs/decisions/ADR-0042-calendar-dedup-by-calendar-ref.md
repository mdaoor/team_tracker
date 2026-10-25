# ADR-0042: Calendar dedup by `calendar_ref`; `source`, `corrects` and the schema minor
Date: 2026-10-25   Status: accepted (plan P15 §15.4; the plan names this ADR-0030, already taken by "filter compiler in server")

## Context
A meeting on a calendar can be sent from two laptops, from the tray and again from the end-of-day card, or re-sent after a client crash.
`client_id` deduplicates a retry of the *same message*, not two messages for the same meeting. Time must never be logged twice, and a person
must be able to take back a send for ten minutes.

## Decision
1. `ClientMessage` gains two optional fields (backwards compatible; the parity test whitelists them like ADR-0004's `mode`):
   `source{origin, calendar_ref{provider, event_id, series_id, instance_start}}` and `corrects` (a `client_id`). `CLIENT_SCHEMA_MINOR = "0.2"`
   is the `client_version` a Phase 15 client sends; the default stays `"0.1"` and the server accepts both.
   `origin` is `manual | assisted | personal | calendar:google | calendar:microsoft`.
2. **Dedup key**: `(workspace, person, calendar_provider, calendar_event_id, calendar_instance_start)` on `time_entries`
   (new nullable columns `workspace_id`, `client_id`, `calendar_provider`, `calendar_event_id`, `calendar_instance_start`; migration `0013`).
   A partial **unique** index `uq_time_entries_calendar_instance` (`WHERE calendar_event_id IS NOT NULL AND deleted_at IS NULL`) is the backstop;
   ingest checks first and answers `status: deduplicated` with the original item, writing no event. Only messages that log minutes are
   keyed: a `note` about a meeting is not a duplicate. An unlogged (soft-deleted) entry frees the key, so a deliberate re-send works.
   `instance_start` is the occurrence start, so every instance of a recurring series logs once and the next week's does not collide.
3. **`time.unlog`** is `ClientMessage{action: update, corrects: <client_id>}` (the `ClientAction` enum is fixed by the spec, so no new
   action). It soft-deletes the time entry written for that `client_id`, emits `time.unlogged` in the same transaction, and is itself
   idempotent by its own `client_id`. ACL: only the person who logged it; anything else behaves as "not found" (ADR-0008) with warning
   `unlog_target_not_found` and a `time.unlog_ignored` event so the message stays idempotent. The ten-minute window is enforced by the client
   (an undelivered batch is simply discarded from the outbox); the server does not impose one.
4. Titles, attendees and locations of a calendar entry are **not** part of the message; only the person's `note` is.

## Consequences
* Two devices, a retry after a lost response and the tray plus end-of-day card all converge on one time entry.
* `time_entries` grows five nullable columns instead of a new table (no change to the 65-model parity test or the extras list).
* Two different people on the same meeting each log their own entry (the key includes the person) - intended.
* A meeting whose time changed on the calendar after being sent has a new `instance_start` only if the provider reports one; a send of the
  moved occurrence can therefore be logged a second time. The person can Unsend the first.

## Alternatives considered
* Dedup on `Event.payload` JSON: no usable partial unique index, slow scans.
* A new `calendar_refs` table: more joins, and an extra model to keep in step with the spec.
* A new `ClientAction.UNLOG`: breaks schema parity with `docs/spec/team_tracker_models.py`.
