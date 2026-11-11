# ADR-0019: Write tools are per-task opt-in; the agent never bypasses the capture API
Date: 2026-10-24   Status: accepted

## Context
An agent that can post to the server can be talked into posting the wrong thing by the very text it is processing.

## Decision
The tool registry contains write tools, but no task offers them to the model. `send=true` on `/v1/agent/capture` is executed
by gateway code *after* validation and grounding, through the normal capture API, with the caller's own token and an
`X-Agent-Run-Id` header the server copies into `payload.agent_run_id` of every event it writes. The gateway's service token is
read-scoped. A tool call outside a task's allow-list is refused, logged (`tool_blocked`) and visible in the run trace.

## Consequences
Prompt injection cannot cause a write the caller could not make anyway, and cannot cause a write at all unless the caller asked
for `send`. Review-queue and classification writes made on the model's behalf go through the same services as human ones and are
stored as unconfirmed suggestions. Giving a future task a write tool means naming it in that task's allow-list and adding an
injection test for it.
