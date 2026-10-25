# ADR-0052: Management commands may be rejected; capture may not
Date: 2026-10-25   Status: accepted (Phase 14; plan 16 "ADR-0028" slot, renumbered because 0028 was taken)

## Context
Rule 1 says capture never blocks: a reporter's line is accepted, filed in the Inbox and reviewed later. A management command changes the
shared overlay (projects, teams, stages, approvals) and is subject to ACL, validation and business rules. Applying rule 1 to commands would
let anyone write anything; applying "may be rejected" to capture would stop people reporting.

## Decision
* Capture messages keep rule 1 unchanged.
* Commands are validated against the op's JSON Schema and authorised with `acl.require` on the person's real roles. A refusal is a
  per-command result `{status: "rejected", errors: [{pointer, code, message}]}`, **not** an HTTP error for the batch (`207`). A rejected
  command writes nothing and does not affect applied ones in the same batch.
* The client makes the difference visible: capture rows have no Confirm step; commands always show an **Effect preview** (a
  `preview_only` call that never writes) and need one confirmation; destructive or wide commands need a second one even with `--yes`;
  only `--force` skips it and is recorded in `_meta.forced`.
* Role modes are a lens in the client (which verbs are offered first). They are never a permission: the server decides.
* The assisted translator proposes, never applies. The gateway offers the model only ops in the server's catalogue, validates every command
  against the op's schema, grounds entities in the pack / tool results / the person's words and drops the rest. A rejected command is parked
  in `outbox/rejected/` with the server's reasons and is not retried.

## Consequences
* Two result vocabularies in one response: messages `applied | deduplicated | applied_with_warnings | failed`, commands
  `applied | rejected | previewed | deduplicated`.
* The CLI exits non-zero when a command is rejected; a capture-only run keeps its exit codes.
* A forged command from a capture-scoped token must be rejected with `403 scope` by the server (server work, not client work).

## Alternatives considered
* Treat a rejected command as a review-queue row (like capture): hides a failed instruction behind a manager's inbox; a person asking to
  merge two tasks needs an answer now.
* Make `--yes` enough for destructive ops: one stray flag in a shell history could merge items; `--force` is deliberate and auditable.
