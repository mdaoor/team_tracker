# ADR-0027: `state_id` is tagged overlay but capture moves it
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
`WorkItem.state_id` is an overlay-tagged column in the object model (§7) but the capture path changes it for `done`, `reopen` and `start` messages.

## Why
Reporters say "done" in the same sentence as everything else; routing that through a manager step would break the capture-never-blocks rule.

## Consequences
The layer-guard test carries an explicit exemption for `state_id`. The stage/state sync (ADR-0005) keeps the stage placement consistent.
