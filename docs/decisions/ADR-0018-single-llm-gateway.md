# ADR-0018: One LLM gateway on LiteLLM; clients and the core server are LLM-agnostic
Date: 2026-10-24   Status: accepted

## Context
Translation, agentic capture, summaries, suggestions and policy drafting all need models, with different privacy needs
(laptop-only vs team box vs cloud). Scattering provider calls across the CLI, desktop, Slack bot and server would
duplicate prompts and make "nothing leaves the host" unprovable.

## Decision
`apps/llm_gateway` is the only code that holds prompts, picks models or talks to a provider. Clients send a small envelope
and get validated JSON back; the core server calls it through `LLMGatewayClient`. Models are reached with
`litellm.acompletion`; alias fallbacks, retries, privacy filtering and budgets are our own code around it (`routing.py`)
rather than `litellm.Router`, so every rule is unit-testable with a scripted backend. The gateway imports `tt_schema` and
`tt_client` (parser, pack and post-processing) as libraries; it does not import the server.

## Consequences
Switching a task between Anthropic and Ollama is a config change. Failure of the gateway never blocks capture: clients fall
back to the structured parser (`GatewayTranslator` returns `None` on any error, including 429), the server skips suggestions.
The price is one more service to deploy; the laptop sidecar (`tt gateway serve`) removes that for individuals.
