# LLM gateway (`apps/llm_gateway`, package `tt_llm`)

All LLM logic lives in this one service. The `tt` client and the core server never talk to a model: they call the gateway,
and the gateway calls models through LiteLLM. Prompts, routing, privacy modes and budgets are configured here and nowhere else.

```text
tt / desktop / Slack ──► /v1/translate, /v1/agent/capture, /v1/agent/batch-review ──┐
core server (jobs)   ──► /v1/suggest/*, /v1/summarise/*, /v1/policy/author ─────────┤► gateway ──► LiteLLM ──► Ollama / Anthropic / ...
web console (P5/P9)  ──► /v1/rules/dry-run, /v1/policy/author ──────────────────────┘     │
                                                                                          └─► tools ──► core server API (caller's token)
```

## Run it

| Mode | How |
|---|---|
| Laptop sidecar | `tt gateway serve` (`--local-only` is the default: loopback, Ollama only, no token) then `tt login --url ... --gateway-url http://127.0.0.1:8700 ...` |
| Team box / cloud | `tt-llm serve --config /etc/tt/llm_gateway.yaml --host 0.0.0.0 [--certfile cert.pem --keyfile key.pem]`, or `docker compose --profile llm up llm-gateway` |

Config: `apps/llm_gateway/llm_gateway.example.yaml` (aliases, fallbacks, budgets, limits, storage). Secrets come from the
environment: `TT_LLM_GATEWAY_TOKENS` (comma-separated shared secrets clients send as `Bearer`), `TT_LLM_ADMIN_TOKEN`
(needed for `GET /v1/runs/{id}`), provider keys (`ANTHROPIC_API_KEY`, ...). The core server reaches it through
`TT_LLM_GATEWAY_URL` / `TT_LLM_GATEWAY_TOKEN` and the `TT_FEATURES__LLM_SUGGESTIONS` flag.

## Privacy modes

| `privacy_mode` | models allowed |
|---|---|
| `local_only` | `ollama/*`, `ollama_chat/*`, `llamacpp/*` only, and `ollama_api_base` must be on localhost (checked at start-up). Cloud fallbacks are removed from every alias; an alias with no local model answers `503`. Nothing leaves the host. |
| `hybrid` | local models for everything except the aliases in `cloud_aliases` (default: `summariser`) |
| `cloud` | everything configured |

A request may pass `options.privacy_mode` to make itself *stricter*; it can never loosen the server's setting.
`tests/test_routing.py` runs the real LiteLLM backend with a fake cloud key under a socket guard and asserts only loopback
connections are attempted.

## Endpoints

All take one JSON envelope (`workspace`, `actor`, `line`/`lines`, `now`, `time_zone`, `pack`, `options{send, apply,
model_alias, privacy_mode}` plus task fields) and answer `{output, batch?, batches?, confidence, rationale?, run_id, model,
cost_usd, warnings[]}`. Errors are `{error:{code,message}}`; a spent budget is `429` with `Retry-After`.

| Endpoint | Task |
|---|---|
| `POST /v1/translate` | one line + ContextPack -> `ClientBatch`; 1.5 s model timeout; single call, no tools |
| `POST /v1/agent/capture` | agentic capture with read tools; `options.send=true` posts the batch to the core server with the caller's `X-TT-Core-Token` and tags every resulting event with `agent_run_id` |
| `POST /v1/agent/batch-review` | merge/de-duplicate/fill pending outbox files; returns per-file `{action: keep\|merge\|drop, batch, changes}` and `batches` for `tt sync --assist` |
| `POST /v1/summarise/{project\|epic\|product}`, `/daily-log` | facts -> short grounded text |
| `POST /v1/suggest/review`, `/classify` | ranked, candidate-validated suggestions for the review queue / classification pre-fill |
| `POST /v1/policy/author` | plain language -> policy draft (validation tools arrive with Phase 8; `validated` is false until then) |
| `POST /v1/rules/dry-run` | which sample lines parse differently under a candidate rule set |
| `POST /v1/embed` | embeddings (optional) |
| `GET /v1/runs/{id}` | admin: the full trace of a run |

OpenAPI: `docs/llm/openapi.json`.

## Agent loop and tools

`agent/loop.py`: system prompt + reporter text wrapped as data -> up to `max_steps` model turns, each possibly calling tools
(`max_tool_calls` total, arguments validated against the tool's JSON schema, results truncated to `tool_result_max_chars`
with an explicit `truncated: true`) -> one forced, schema-constrained final answer (one repair retry) -> deterministic checks.

Read tools: `get_context_pack`, `search_items` (trigram scores from `GET /items/search`), `get_item`, `resolve_project`,
`glossary_lookup`, `list_people`, `list_milestones`, `list_iterations`, `list_stages`, `list_risks`,
`list_dimension_values`, `recent_activity`; `policy_check` / `policy_test` answer `not_available` until Phase 8.
Write tools (`post_capture_batch`, `resolve_review_item`, `create_classification`) exist in the registry but **no task offers
them to the model**: `send=true` is performed by gateway code through the capture API after validation (ADR-0019).
A call to a tool outside the task's list is refused, logged and shows up in the trace.

Every run is stored (`agent_runs`, `agent_steps`, `budgets` in schema `llm`, or memory without `storage.url`). Reporter text is
not stored unless `log_raw_input: true`; the rationale stays in the trace and is never sent to the core server.

## Safety layers

1. Reporter text is wrapped in `<reporter_text trust="none">` with angle brackets defanged; the system prompt says it is data.
2. `guards.enforce` drops anything the pack and tool results do not contain: item keys, people, milestones, iterations,
   risks, stages, products. Things the person typed themselves (`WEB-142`, `@sara`) count as grounded; the server still checks them.
3. Overlay hints with `llm_confidence < 0.7` are nulled.
4. **Numbers come from the parser**: minutes, dates and estimates are taken from the deterministic parser, never the model
   (a model that spreads "3h" over a list of items is corrected). Consequence: phrasings the parser does not know ("for 90
   min") produce no number. Extend the parser, not the prompt.
5. Syntax the person typed (`!high`, `p:web`, `new task:`, `for: @sara`, URLs) fills fields the model left empty.
6. Budgets: `budgets.default_usd_per_workspace_per_day` with per-workspace overrides; checked before every model call.

## Server-side use

* **Review suggestions**: `review.created` -> job `llm.suggest_for_event`, plus nightly `llm.nightly` and
  `POST /llm/suggest-reviews`. The answer is stored under `ReviewItem.suggestion.llm` (the original suggestion is kept),
  `confidence` is set when empty, event `agent.suggestion_written{agent_run_id}`. Only when `TT_FEATURES__LLM_SUGGESTIONS` and a
  gateway URL are set.
* **Classification pre-fill**: dimensions with `is_required_for` get unconfirmed `Classification{source=rule, confidence}`
  rows (never over a manual one); they appear in the review queue. `POST /llm/prefill-classifications` runs it on demand.
* `GET/PATCH /review-items/{id}`, `POST /classifications:suggested`, `POST /auth/service-tokens` (read-scoped token for the gateway).

## Tests

`apps/llm_gateway/gateway_tests` (89): routing/fallback/privacy/budget/repair, 31 recorded agentic scenarios, the 59-case
translator golden set through the gateway with a deliberately bad model, injection, batch review (13), summaries (10 + alias
swap), suggestions, policy author, rules dry-run, Postgres storage. `apps/server/tests/e2e/test_gateway_e2e.py` drives a real core server over HTTP.

## Not done / not measured

* **No real model was available.** Accuracy of Ollama/Anthropic models on the golden set, translate p95 with a 7B model, and
  agent p95 are **not measured**; the tests prove the pipeline and its guarantees with scripted models. The golden-set run
  with a real model is `pytest -m llm` territory and still to do.
* No Schemathesis contract run on either OpenAPI document.
* LiteLLM's `Router` is not used; fallback, retries, budgets and privacy filtering are implemented in `routing.py` on top of
  `litellm.acompletion` so they can be tested without a provider. Cost comes from `litellm.completion_cost` (zero for local models).
* pgvector is not adopted (ADR-0020); `/v1/embed` returns vectors but nothing stores them.
* Insights (Phase 7) and policy authoring (Phase 9) will call `/v1/summarise/*` and `/v1/policy/author` through
  `LLMGatewayClient`; those callers do not exist yet. The daily-log summary client method exists, no job calls it.
