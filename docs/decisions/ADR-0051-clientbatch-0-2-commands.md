# ADR-0051: `ClientBatch` 0.2 carries management commands next to capture messages
Date: 2026-10-25   Status: accepted (Phase 14; plan 16 "ADR-0027" slot, renumbered because 0027 was taken)

## Context
Managers, scrum masters, coaches and PMs need to send changes from the same client reporters use (create a project, merge items, make a
team, approve). Capture messages are facts that are never rejected (rule 1); management changes are instructions that the server must
authorise. Two files, two endpoints and two outboxes would break "the JSON outbox file is the interchange format" (ADR-0039) and the
offline guarantee.

## Decision
`tt_schema.client` gains `ClientCommand{client_id, actor, at, op, args, on_behalf_of?, preview_only}` and `ClientBatch.commands: list[ClientCommand]`
(default `[]`; `messages` stays required and may be empty). The schema version advertised by clients that know commands is `0.2`
(`CLIENT_SCHEMA_VERSION`); the model default of `client_version` stays `"0.1"` so the spec parity test and old files are unchanged.
One batch may hold both; the server processes `messages[]` first, then `commands[]`, in order, under one `correlation_id`. Every entry has
its own idempotency key (`client_id`) and result. `commands` is accepted by `POST /capture/batches` and by `POST /commands`.

`args["_meta"] = {client_version, mode, role_mode, forced}` is client-owned audit data: the server strips it before validating `args`
against the op's schema and records it in the event payload. A preview is a separate request with `preview_only=true` and a different
`client_id` (`<id>:preview`) so it can never shadow the real command in idempotency.

## Consequences
* An old client and an old file keep working; a 0.2 file sent to an old server is rejected by schema (`commands` unknown) rather than half-applied.
* The outbox, `tt edit/send/sync`, the Slack snippet and the desktop resend work unchanged for command files.
* The capture translator never emits commands: the gateway removes `commands` from the schema it shows the capture model.

## Alternatives considered
* A second file format / endpoint only for commands: loses the single outbox and mixed ordering ("log my work, then merge two tasks").
* Commands as a `ClientAction` on messages: would inherit "never rejected" semantics and the fuzzy matching, which commands must not have.
* Bumping the model default to `"0.2"`: breaks the spec parity test for a field the spec file does not own.
