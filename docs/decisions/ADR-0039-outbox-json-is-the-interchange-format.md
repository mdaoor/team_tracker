# ADR-0039: The JSON outbox file is the interchange format between shells
Date: 2026-10-24   Status: accepted (plan P11 ADR-0017 slot; renumbered because 0017 was taken)

## Decision
Every shell (CLI, desktop, Slack, mobile) turns input into a `ClientBatch` JSON file in the same outbox layout (`outbox/<ts>-<client_id>.json`,
`queue.db` for attempts) and sends from the file. A file written by one shell can be edited, resent or discarded by another; the server
accepts any valid file regardless of how it was produced, and the manual form's mandatory-field rule is a client-side convenience only.
Slack keeps the JSON as a thread snippet; the card's buttons act on the file by id; a replaced card's file is removed so a stale button is
a harmless no-op ("exactly once").
