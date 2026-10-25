# ADR-0029: Agent runs are kept in memory unless a storage URL is configured
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
`agent_runs`/`agent_steps` persist to SQL only when `storage.url` is set; otherwise a bounded in-memory store is used (laptop sidecar).

## Why
The sidecar must start with no database; the server deployment always configures storage.

## Consequences
Run traces are lost on restart without storage. `GET /v1/runs/{id}` is admin-only either way.
