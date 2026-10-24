# Prompt library

Prompts live in the gateway package as `apps/llm_gateway/tt_llm/prompts/<task>/<version>.md` and are the only prompts in the
repository. A task reads `prompt_versions.<task>` from the config (default `v1`), so a new prompt version is a new file plus a
config line, and the old one stays available for comparison.

| Task | Prompt | Used by |
|---|---|---|
| `translate` | rules 1-6 of UX §2.4, `<<PACK>>` slot | `/v1/translate`, `/v1/rules/dry-run` |
| `agent_capture` | tool-use instructions, no questions to the reporter | `/v1/agent/capture` |
| `gap_fill` | choose a project from candidates | `/v1/agent/batch-review` |
| `summarise` | facts-only, <= 120 words, <= 1 risk | `/v1/summarise/{scope}` |
| `daily_log` | first-person day summary | `/v1/summarise/daily-log` |
| `suggest_review` | rank candidate resolutions | `/v1/suggest/review` |
| `suggest_classify` | pick dimension values | `/v1/suggest/classify` |
| `policy_author` | template-first policy drafting | `/v1/policy/author` |

Every prompt is followed by the shared guard text (`guards.SYSTEM_GUARD`). Adding "ask the model" to a later phase means a new
`tasks/<name>.py`, a prompt here, a test with recorded model output, and a thin client call elsewhere.
