# ADR-0046: `TT_SECRET_KEY` is required outside dev and must differ from the session secret
Date: 2026-10-25   Status: accepted (Phase 13 R3.9; review "below the top 15")

## Decision
`validate_for_runtime` rejects `env != dev` without a `TT_SECRET_KEY` of at least 32 bytes, or equal to `TT_SESSION_SECRET`. The Fernet
key for stored integration credentials derives from it only; the dev fallback to the session secret remains for `env=dev` alone.

## Consequences
Rotating the cookie-signing secret no longer makes stored Jira credentials unreadable (and vice versa). Deployments must set both;
`docs/ops/config-reference.md` and the Helm secret list say so.
