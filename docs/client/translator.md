# Translator, parser and client modes

`context_schema_version`: **1** (ContextPack). `ClientBatch.client_version`: `0.1`.

## Pipeline (`tt_client.parser.llm.translate_line`)
1. `StructuredParser` parses the line deterministically (works with no model).
2. If a gateway is configured, `GatewayTranslator` posts `{line, actor, now, time_zone, pack(compact)}` to
   `/v1/translate` (or `/v1/agent/capture` when `llm.mode = "agent"`) with a 1.5 s timeout.
3. `postprocess` repairs the answer: actor/at/client_id/raw_input are set by the client; parser numbers, dates, tags,
   typed item keys and `on_behalf_of` win (ADR-0003); people, milestones, iterations, risks and item keys absent
   from the pack are dropped; overlay hints with `llm_confidence < 0.7` are nulled.
4. On any failure the parser result is used and the card says “will be sorted by your manager”. Nothing is lost.

## Prompt (owned by the gateway, `prompts/translate/`; skeleton from UX §2.4)
1. You convert one chat line into `ClientBatch` JSON. Output JSON only.
2. Prefer matching `my_open_items` by meaning over creating new items; put the match in `item.key` and set `llm_confidence.item`.
3. Resolve project via `projects.aliases`, else `glossary`, else leave null (server → Inbox).
4. Apply `manager_rules` literally.
5. Never invent people, milestones, or risks not in the pack.
6. Set `llm_confidence` per filled overlay field; anything < 0.7 stays null.

Output is constrained by `GET /api/v1/meta/schema/client-message?batch=true` (`client_message_json_schema(batch=True)`).
Model requirements (gateway side): a 3–8 B instruct model with JSON-schema constrained decoding; < 1.5 s on CPU.

## Syntax accepted without a model
`doing|on|started|working on|picking up|switching to` · `done|finished|shipped|closed` · `pause|parking` ·
`resume|back on` · `blocked|stuck` · `note:` · `new task:|add bug:|create` · `#tag` `@person` `p:web` `!high` `for: @sara`
· `KEY-123` · URLs (PR/commit detected) · `took 2h|spent 45 min|1h30m|2d` (d = `hours_per_day`, default 8) ·
`plan|eta|by|should be done by <date>` · `est|probably|about 3d|5 points` · numbered lists `1. a, 2. b`.
With no verb: minutes → `log_time`, a plan → `plan`, an estimate → `estimate`, otherwise `note` (configurable).
**List rule:** time / plan / estimate given once apply to the **first** item unless each element carries its own.
Dates use the reporter's time zone; no time of day means 17:00; `eod` = 17:00.

## Modes
- **Assisted** (`tt "…"`): text → batch → `outbox/<ts>-<client_id>.json` (reporter-owned, pretty-printed, `_preview`
  stripped before sending) → card → `Enter`/`--yes` sends. `tt edit <id>` opens `$EDITOR`, re-validates, records
  corrections (opt-in feedback); `tt send <id> [--jira]`; sent files move to `outbox/sent/`, 422s to `outbox/quarantine/`.
- **Manual** (`tt form`): choices come only from the ContextPack; mandatory fields come from
  `pack.manual_mode.required_fields` (default `action,item,project`; per-project overrides; `minutes` is implied for
  `log_time`, `item_type` for `create`). `tt form --from <id>` loads an assisted file; `--answers file.json` is the
  non-interactive form. Batches carry `mode: "manual"`. The server never rejects for missing fields.
- **Air-gapped:** `tt pack --import pack.json` → `tt form` → `tt sync` later.

## Offline
`outbox/` + `queue.db` (attempts, last error). `tt sync` sends oldest first, stops at the first network error, keeps
only the failed messages after a 207, quarantines on 422. `client_id` makes delivery exactly-once.

## Evaluation
`tt-eval translator --golden packages/client_core/tests/golden/lines.yaml [--subset syntax]` prints per-field accuracy.
Structured parser, 59 cases: **1.000** on every field (the syntax subset is a CI gate). Model-in-the-loop numbers
(target: action ≥ 0.9, item ≥ 0.85, minutes ≥ 0.9, planned_end day ≥ 0.85) need a real model and are **still not
measured**. Phase 2b (`docs/llm/gateway.md`) adds the harness side: the same 59-case golden set is replayed through the gateway
with a deliberately bad scripted model and the syntax subset stays at 1.000 on every field, because minutes, dates, estimates, tags
and typed keys always come from the parser.

## Management commands (phase 14)

`POST /v1/translate/command` (and the agentic `/v1/agent/command`) turn a management request into `ClientCommand[]`. The gateway reads the
command catalogue from the core server with the caller's token (`X-TT-Core-Token`, cached per workspace by ETag), shows the model only those
ops (prompt `prompts/translate_command/v1.md`, one few-shot example per op taken from the catalogue), validates every command against its
schema and grounds people / projects / keys. It never applies anything. Golden: `packages/client_core/tests/golden/command_lines.yaml`
(84 lines); scripted-model gate op ≥ 0.85, args ≥ 0.90; opt-in real-model run in `test_real_model_golden.py`.
