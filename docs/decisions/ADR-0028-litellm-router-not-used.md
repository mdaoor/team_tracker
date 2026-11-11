# ADR-0028: The gateway uses `litellm.acompletion` with its own routing, not the LiteLLM `Router`
Date: 2026-10-24   Status: accepted (records an existing deviation)

## Decision
Alias resolution, privacy-mode routing, fallbacks, retries and budgets are implemented in `tt_llm/routing.py` on top of `litellm.acompletion`.

## Why
The privacy rules (local_only never leaves the host, redaction levels per alias) and the per-workspace budget must sit *before* provider selection; the `Router` object hides provider choice and its retry policy is not auditable.

## Consequences
More code to maintain and test (89 scripted tests). Swapping to the `Router` later is possible behind the same `Backend` protocol.
