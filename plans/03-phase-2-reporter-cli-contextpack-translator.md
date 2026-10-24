# Phase 2 — Reporter client core: ContextPack, regex parser, CLI, local LLM translator

## Goal
A reporter can type `tt "fix the login redirect loop, took 2h, plan to finish tomorrow 5pm"` (or structured `tt doing "fix login" -p web #backend`) and within ~1 s see the parsed card and have the batch accepted by the server, online or offline. The translator uses the server‑published **ContextPack** and emits `ClientBatch` JSON via JSON‑schema constrained decoding. The same core library will power the desktop palette, mobile and Slack bot in P11.

## Depends on
Phase 1.

## Inputs
- UX §2 (all), §7.1
- `tt_schema.client` and `client_message_json_schema()`
- Object model §12

## Scope
1. **ContextPack** (server): `GET /api/v1/w/{ws}/context-pack` producing exactly the JSON in UX §2.4, personalised per actor; ETag + `If-None-Match`; refreshed by clients on login and every 15 min.
2. **Glossary & translator rules** (server): tables `glossary_entries(workspace_id, project_id?, phrase, resolves_to_type, resolves_to_id, source manual|learned, hits)` and `translator_rules(workspace_id, scope_type, scope_id?, text, is_active, position)`; CRUD; both flow into the pack. (The UI comes in P5.)
3. `packages/client_core` (`tt_client`):
   - `parser/structured.py`: deterministic parser for the syntax forms (`#tag`, `@person`, `p:project`, `!high`, `took 2h`, `est 3d`, `plan tomorrow 5pm`, verbs table UX §2.3) → `ClientMessage`. Works with no model.
   - `parser/llm.py`: `LLMTranslator` protocol + `GatewayTranslator` (HTTP client of the Phase 2b LLM gateway: `/v1/translate` fast path, `/v1/agent/capture` when `llm.mode=agent`) + `NullTranslator` (structured parse only). No provider SDK or Ollama call lives in the client; the gateway may run locally (`tt gateway serve --local-only`).
   - `pack.py`: ContextPack cache (disk, ETag), refresh policy.
   - `queue.py`: offline queue (SQLite file), `client_id` generation (uuid7), retry with backoff, batch coalescing ("doing 1,2,3" → one batch).
   - `preview.py`: builds the preview rows (Task / Project / Time / Plan / Tags / Stage) with confidence cues (`✓ matched`, `92 %`, `suggested`, `ask`) from `ClientMessage` + `llm_confidence` + pack.
   - `feedback.py`: records user corrections (Tab‑edit) as `(raw, field, old, new)` → `POST /capture/feedback` (opt‑in) for later fine‑tuning; server stores in `translator_feedback`.
4. `apps/cli` (`tt`): commands `tt <free text>` (assisted mode), `tt form` (manual mode), `tt doing|done|pause|resume|blocked|note|time|plan|est|new`, `tt edit|send|outbox` (JSON file workflow), `tt now` (active timer), `tt today` (daily log draft, `--confirm`), `tt sync`, `tt login`, `tt pack --refresh|--import|--export`. Output: the preview card; `--json`; `--dry-run`. See 2.6 for the two modes.
5. **Prompt engineering** for the translator: system prompt per UX §2.4 skeleton; few‑shot examples; evaluation harness with a golden set of ≥ 60 lines → expected `ClientMessage`s (store under `packages/client_core/tests/golden/`).
6. End‑of‑day: `tt today` + notification hook (CLI prints; desktop in P11).

## Out of scope
GUI shells (P11), Jira chip (P10), stage/product hints beyond passing them through (P3 adds the server side; the translator already emits them and the P1 ingestion stores unknown ones as `ReviewItem`s).

## Deliverables
- Server: context‑pack endpoint, glossary/rules CRUD, feedback endpoint.
- `tt_client` library with ≥ 90 % coverage on the structured parser and queue.
- `tt` CLI installable via `uv tool install`.
- `docs/client/translator.md`: prompt, schema, model requirements, evaluation results.

## Detailed tasks

### 2.1 ContextPack assembly (server)
Fields and sources (all filtered by what the actor can read — P1 workspace scope, tightened in P4):

| field | source |
|---|---|
| `actor`, `workspace`, `generated_at` | request |
| `projects[]` | non‑inbox projects where actor is a `ProjectMember` or in `Project.team_id`'s team, plus projects the actor touched in 30 d; `aliases` = `settings.aliases` + glossary phrases that resolve to the project; `process`, `default_stage` null until P3; `jira` = `external_refs.jira` |
| `my_open_items[]` | items with assignee=actor or an active `Assignment`, state category in open/active, ordered by last event desc, max 200 |
| `people[]` | members of the actor's teams + anyone they share a project with |
| `tags[]` | non‑provisional tags used in the actor's projects in 90 d, max 300 |
| `milestones[]`, `iterations[]` | upcoming/active in the actor's projects |
| `stages{}` | empty object until P3 |
| `priorities[]` | enum values |
| `risks[]` | empty until P3 |
| `manager_rules[]` | active `translator_rules` for workspace + actor's projects/teams, ordered by `position` |
| `glossary{}` | phrase → `"PROJECT:KEY"` / `"ITEM:KEY"` / `"PERSON:@h"` strings |

Size budget 5–30 KB: truncate lists at the maxima above and set `truncated: true`. Cache per actor for 60 s server‑side.

### 2.2 Structured parser (deterministic)
Grammar (all pieces optional, order‑free, separated by `,`/`|`/newline):
- Verb at start: `doing|on|started|start|working on` → `start`; `done|finished|shipped|closed` → `done`; `pause|parking|park` → `pause`; `resume|back on` → `resume`; `blocked|stuck` → `blocked`; `note:` → `note`; `new task:|add bug:|create` → `create` (+ `item_type`); `took|spent` → `log_time`; `plan|eta|by` → `plan`; `est|estimate|probably|about` → `estimate`.
- `took 2h`, `spent 45 min`, `1.5h`, `2d` → minutes (`d` = 8 h, configurable `hours_per_day` in pack later).
- Dates: `tomorrow 5pm`, `friday`, `eod`, `next week`, ISO; use `dateparser` with the person's zone; `eod` = 17:00 local.
- Numbered lists `1. X, 2. Y` or `1) X 2) Y` → multiple messages, same verb.
- Tokens: `#tag`, `@person`, `p:web` / `project:web`, `!high` / `prio:high`, `for: @sara` → `on_behalf_of`, URLs → `evidence{kind: link|pr|commit}` (github PR/commit URL patterns).
- Everything left over = `item.title` (trimmed, lower‑cased for matching but original kept in `raw_input`).

### 2.3 LLM translator
- The prompt and model live in the gateway (Phase 2b, `prompts/translate/`); this section specifies what `/v1/translate` must do and what the client does with the result. Request: system prompt (UX §2.4 skeleton, numbered rules 1–6 verbatim) + compact pack (only `projects{key,name,aliases}`, `my_open_items{key,title}`, `people{handle}`, `tags`, `milestones{name}`, `iterations{name}`, `manager_rules`, `glossary`) + the user line. Response: `ClientBatch` constrained by `client_message_json_schema()` wrapped in a batch schema.
- Post‑processing: run the structured parser too; for `minutes`, `planned_end`, `estimate`, `tags` prefer the deterministic result when both exist and differ (numbers are where small models err). Fill `raw_input`, `client_id`, `at`, `actor`. Drop any `people/milestones/risks` not present in the pack (rule 5). Null any overlay field with `llm_confidence < 0.7` (rule 6).
- Timeouts: 1.5 s → fall back to structured parse and mark preview rows "will be sorted by your manager" (UX §2.6).
- Client config in `~/.config/tt/config.toml`: `[llm] gateway_url="https://localhost:8800" mode="translate"|"agent"`; `[server] url=… ca_bundle="/etc/tt/ca.pem"` (PEM trust for internal CAs, see Phase 4 §4.6). Model choice, provider keys and privacy mode are gateway‑side (Phase 2b).
- Evaluation harness `tt-eval translator --golden …` prints per‑field accuracy; target ≥ 0.9 on `action`, ≥ 0.85 on `item` match, ≥ 0.9 on `minutes`, ≥ 0.85 on `planned_end` day.

### 2.4 Offline queue & sync
- SQLite at `~/.local/share/tt/queue.db`: `pending(client_id pk, batch_json, created_at, attempts, last_error)`.
- `tt sync` and automatic sync on any command: send oldest first; `207` responses mark individual messages failed/applied; keep failed with error; `422` (schema) → quarantine file and tell the user (this is a client bug).
- Preview shows a queued badge when offline.

### 2.5 CLI UX details (UX §2.2, §2.6)
- Card rendering with `rich`: rows exactly as in the palette mock; confidence column.
- `--edit field=value` overrides and records feedback.
- `tt now` shows the active activity and elapsed time (from server `GET /activities?status=active&person=me`, cached locally when offline from the queue).
- `tt today`: fetch `DailyLog` draft; `--confirm` posts confirm; editable summary via `$EDITOR`.
- First run: prints the three ghost examples and asks for the server URL + token (`tt login` opens the browser to `/auth/cli` which mints a token — P4 wires OIDC; P2 accepts paste).

### 2.6 The two client modes (product requirement, added after the UX doc)
Every client shell (CLI here, desktop/mobile/Slack in P11) exposes **two sending modes**. Both end in the same `ClientBatch` JSON and the same `POST /capture/batches`; they differ in who fills the JSON.

#### Mode A — Assisted (free text → LLM → JSON file → server [+ Jira])
1. Reporter types free text.
2. The LLM gateway (`/v1/translate`, or `/v1/agent/capture` in agent mode — Phase 2b) plus the local structured parser produce a `ClientBatch`. `tt sync --assist` can additionally send the whole outbox through `/v1/agent/batch-review` to merge fragments and fill gaps before sending; the user confirms the diff.
3. The batch is **written to a local JSON file the reporter owns**: `~/.local/share/tt/outbox/<timestamp>-<client_id>.json` (pretty‑printed, with `raw_input`, `llm_confidence` and a `_preview` block that is stripped before sending). The card is shown.
4. Reporter may: send as is (`Enter`), open the file in `$EDITOR` (`tt edit <id>` / `e` in the palette) and re‑validate it against the schema, or discard. Edits are recorded as feedback (2.3).
5. `tt send <file|id>` (or `Enter`) validates against `client_message_json_schema()`, pushes the batch to the server, and if `--jira` is set (or the resolved project has `settings.jira.auto_send=true` and the user has not toggled it off) sets `message.jira = {"project": <jira key>, "issuetype": <mapped type>, "send": true}` so the **server** creates/updates the Jira issue (UX §2.5 — the client never holds Jira credentials). Until P10 ships, the server stores the `jira` block on the `Event.payload` and returns `jira: {status: "deferred"}`.
6. Sent files move to `outbox/sent/`; failed ones stay with `last_error` in a sidecar `.err` file. `tt outbox` lists them.

#### Mode B — Manual (import context → fill a form → send)
1. `tt pack --refresh` (or automatic) imports the ContextPack; `tt pack --import <file.json>` loads one from disk for air‑gapped use (manager exports it from the console in P5).
2. `tt form` (CLI: interactive prompts with `questionary`; desktop in P11: a form panel) walks the reporter through the fields, every choice list fed from the pack:
   - **mandatory** (client‑enforced, configurable): `action`, `item` (pick from `my_open_items` or type a new title), `project` (pick), `item_type` (pick, only for new items), `time` (`minutes`) when `action in (log_time, done)`;
   - **optional**: `tags` (multi‑select from pack + free entry → provisional), `priority`, `planned_end` (date picker / natural text), `estimate`, `assignees`, `milestone`, `iteration`, `stage` (when the pack has `stages`), `product`, `risks`, `note`, `evidence` URLs, `on_behalf_of`, `custom_fields` (from `pack.custom_fields[]`, added to the pack in this phase: `{key,name,type,options,required}`), `jira` toggle.
   - Which fields are mandatory comes from `pack.manual_mode.required_fields` (default `["action","item","project"]`), set per workspace/project by managers (`Workspace.settings.manual_mode` / `Project.settings.manual_mode`; UI in P5). **The server still never rejects** — mandatory‑ness is a client‑side convenience so a reporter who *chooses* manual mode gets a complete record.
3. The form produces the same `ClientBatch` file in `outbox/` (with `source.mode = "manual"`, `llm_confidence` absent) and continues at Mode A step 4.
4. `tt form --from <id>` opens an existing outbox file in the form (so an LLM parse can be corrected field by field without hand‑editing JSON).

#### Shared rules
- Mode is chosen per invocation (`tt` = assisted, `tt form` = manual) and remembered as a default in `config.toml` (`[client] default_mode = "assisted" | "manual"`). When no LLM is reachable, assisted mode falls back to the structured parser and offers to switch to the form (UX §2.6 failure case).
- The JSON file format **is** `ClientBatch` plus the ignorable `_preview` key; nothing else. Clients from P11 read/write the same files, so a reporter can draft on mobile and send from the desktop.
- `ClientMessage.source` is not part of the schema; put the mode in `ClientBatch.client_version` suffix (`"0.1+manual"`) **or** add an optional `mode: "assisted"|"manual"` field to `ClientBatch` — do the latter and record it as ADR‑0004 (schema addition, backwards‑compatible). The server stores it in `Event.payload.mode` so managers can later see which mode produces cleaner data.

## Data & API contracts
- `GET /api/v1/w/{ws}/context-pack` → UX §2.4 JSON plus `custom_fields[]`, `manual_mode.required_fields[]`, `jira.{enabled, auto_send}` per project; `ETag`, `Cache-Control: private, max-age=60`.
- `GET/POST/PATCH/DELETE /api/v1/w/{ws}/glossary`, `/translator-rules`.
- `POST /api/v1/w/{ws}/capture/feedback` body `[{client_id, field, raw, previous, corrected}]`.
- `GET /api/v1/w/{ws}/activities?status=active&person=me`.

## Acceptance criteria
- [ ] Golden set accuracy targets in 2.3 met with the default model on CPU (document the machine); structured parser alone passes 100 % of the syntax‑form subset.
- [ ] `tt "doing 1. fix login, 2. write tests, 3. deploy | took: 2h | plan: tomorrow 5pm"` yields a `ClientBatch` with 3 `start` messages, `minutes=120` on the first (document the rule: time/plan apply to the first item unless each list element has its own), `planned_end` tomorrow 17:00 local.
- [ ] With Ollama stopped, every command still works and the card shows the fallback text; nothing is lost; `tt sync` later delivers it exactly once.
- [ ] ContextPack for `@mahmoud` in the seed includes projects WEB, MOB, DATA, GOV (he has items in all), `my_open_items` sorted by recency, size < 30 KB.
- [ ] Translator never emits a person/milestone/risk absent from the pack (property test with adversarial lines like "tell @nobody").
- [ ] `tt now`, `tt today --confirm` work end to end against the dev server.
- [ ] Feedback endpoint stores corrections; the golden set gets a `from_feedback/` folder script.
- [ ] **Mode A**: `tt "fix the login redirect loop, took 2h"` writes `outbox/<id>.json`; editing `minutes` to 90 in `$EDITOR` and `tt send <id>` results in a `TimeEntry` of 90 minutes and a feedback record for `minutes`; the file moves to `outbox/sent/`.
- [ ] **Mode A + Jira**: `tt send <id> --jira` sets `message.jira.send=true`; the server (pre‑P10) stores it and answers `jira: {status: "deferred"}`; after P10 it returns the Jira key.
- [ ] **Mode B**: `tt form` with the seed pack refuses to continue past a missing mandatory field, lists exactly the projects/tags/people/milestones of the pack, produces a valid `ClientBatch` with `mode: "manual"`, and the server accepts it identically to Mode A.
- [ ] `tt form --from <id>` round‑trips an assisted file without losing any field.
- [ ] Air‑gapped: `tt pack --import pack.json` + `tt form` + `tt sync` later works with the server unreachable at form time.

## Exit checklist
- [ ] `docs/client/translator.md` includes the exact prompt and schema version (`context_schema_version` for the pack: start at 1).
- [ ] `tt --help` reviewed for wording against UX §2.3 table.
- [ ] ADR‑0003 "Deterministic parser wins over LLM for numbers and dates".

## Handover notes
P3 fills `stages`, `risks`, `default_stage`, `process` in the pack; P11 reuses `tt_client` unchanged. Keep `tt_client` free of any terminal‑specific code (rendering lives in `apps/cli`).

> **Update (Phase 2b built):** the translator is a thin client of the gateway (`GatewayTranslator`; `tt gateway serve` runs a local sidecar). Prompts and model calls live in `apps/llm_gateway`; see `docs/llm/gateway.md`.
