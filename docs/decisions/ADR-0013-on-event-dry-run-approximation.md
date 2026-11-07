# ADR-0013: Dry-run of `on_event` policies is an approximation
Date: 2026-10-24   Status: accepted

## Decision
`POST /policies/{key}:test` evaluates a condition against the current state of the workspace (candidates selected by the policy's filter
and scope, then the CEL condition), without writing violations, events or notifications. For an `on_event` policy there is no real
triggering event in a dry run, so the `event` root is blank and conditions that read `event.*` evaluate against nothing.

## Consequences
The preview answers "which targets would match now", not "which past events would have fired". The response carries `approximation: true` for such policies. Exact replay of historical events is not offered; scheduled policies are exact (they take an `as_of`).
