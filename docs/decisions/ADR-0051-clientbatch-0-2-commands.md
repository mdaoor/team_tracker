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
(`CLIENT_SCHEMA_VERSION`); the model default of `client_version` was first kept at `"0.1"` (see the addendum: moved to `"0.2"`).
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

## Addendum (2026-10-28): the default moves to "0.2"
Decision: `ClientBatch.client_version` now defaults to `"0.2"` in `docs/spec/team_tracker_models.py` **and** `tt_schema.client` together (the parity
test compares the two defaults, so they cannot drift). Reason: every client that exists sends `0.2`; the stale `"0.1"` default only
mislabelled batches built without an explicit version (tests, hand-written files). The server still accepts `0.1` and `0.2` and does not branch
on the value; it is recorded in `source.client` / `payload.command.client_version` for audit. `DOCUMENTED_ADDITIONS` is unchanged (this is a
default, not a field). The JSON schema default in `docs/api/openapi.json` and the web `schema.d.ts` change accordingly.
