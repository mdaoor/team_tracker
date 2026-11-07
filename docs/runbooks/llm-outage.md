# Runbook: LLM gateway or model outage

Everything LLM-backed degrades, nothing blocks capture.

| Feature | Behaviour during an outage |
| --- | --- |
| `tt say` / palette / Slack | the structured parser answers ("model unavailable: used the structured parser"); rows may need manual confirmation |
| Review suggestions | no suggestions are written; the queue still works |
| Insights / daily-log summaries | the deterministic fallback text is stored with `source=fallback` |
| Generate with AI | `503 gateway_unavailable`; use the manual builder or the external pack/import path |
| Budget exceeded | `429 budget_exceeded` with `retry_after_s`; raise `budgets` in `llm_gateway.yaml` or wait |

Check: `curl $GATEWAY/healthz`, gateway logs, provider status page; for a local model `ollama ps`. Privacy: in `local_only` mode a cloud fallback is never used.
