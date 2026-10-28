# Appendix C — Open decisions, assumptions and ADR template

The spec is silent or ambiguous in the places below. Each row records the default this plan set adopts so the implementer never stalls. Overriding a default requires an ADR.

| # | topic | spec status | default adopted | where |
|---|---|---|---|---|
| 1 | Event sourcing depth | "Event log is source of truth; tables are projections" | Transactional projection (event + table in one tx), `tt-admin verify-projection` in P12 instead of a replay rebuild (ADR‑0040: payloads are deltas); no replay‑only reads | Arch §4.1, ADR‑0001 |
| 2 | Backend language | Models are Python | Python 3.12 / FastAPI / Postgres | Arch §2 |
| 3 | Fuzzy match | "fuzzy match" only | `pg_trgm` similarity ≥ 0.6 with ≥ 0.1 gap, recency bonus; else create | P1 §1.4, ADR‑0002 |
| 4 | LLM confidence threshold | "< 0.7 stays null" (UX) | Client enforces; server re‑checks when `llm_confidence` present | P1 step 6 |
| 5 | Numbers vs LLM | — | Deterministic parser wins for minutes/dates/estimates | P2, ADR‑0003 |
| 6 | Client modes | Added by product owner during planning | Two modes (assisted JSON‑file flow; manual form); `ClientBatch.mode` optional field | P2 §2.6, ADR‑0004 |
| 7 | Stage → state category defaults for the builtin process | Field required, mapping unspecified | idea/discovery/design → open; build/test/staging → active; production/retired → done | P3 §3.3, ADR‑0005 |
| 8 | Review queue ownership tie | "never appears twice" | primary line manager if also PM of that project, else project owner | P3 §3.8, ADR‑0006 |
| 9 | Weight formula grammar | Example only | base × multipliers × clamp JSON grammar | P3 §3.5, ADR‑0007 |
| 10 | Multiple `delivers` products per project | — | Exactly one `delivers`, many `supports` | P3 §3.4 |
| 11 | Required evidence on transition for reporters | Rules may require evidence | Reporters: place + review row (non‑blocking); managers: 409 | P3 §3.3 |
| 12 | 404 vs 403 | — | 404 for invisible objects | P4, ADR‑0008 |
| 13 | Coaching note visibility | Habits visible to subject | Coaching notes manager‑only | P9, ADR‑0014 |
| 14 | `evidence_consistency` habit | Generator uses it; spec lists `evidence_gap` | Add positive‑polarity definition | P8, ADR‑0012 |
| 15 | Dry‑run for `on_event` policies | "dry‑run against last 30 days" | Approximate replay from event payload + current rows | P8, ADR‑0013 |
| 16 | Jira direction | UX §2.5 | Server‑side push; inbound status/assignee/comments only | P10, ADR‑0015 |
| 17 | Desktop ↔ `tt_client` bridge | — | Python sidecar (PyInstaller) in v1 | P11, ADR‑0038 (ADR‑0016 was taken by propagation) |
| 18 | Snapshot range semantics | — | Items touched/done/planned in range + all open/active | P7 §7.1 |
| 19 | Points → hours | — | Points not converted (`estimate_h=null`); days × `hours_per_day` (default 8) | P7 |
| 20 | Inbox item keys | — | `INBOX-<handle>-<n>`, re‑keyed on move, old key kept in `external_refs.previous_key` | Arch §4.7 |
| 21 | Multi‑tenancy | Workspace is tenant | Row‑level `workspace_id` scoping, single DB | Arch §4.6 |
| 22 | Attachment storage | — | Local disk in dev, S3 adapter in P12 | P1, P12 |
| 23 | Time zone for dates | `Person.time_zone` exists | Person zone for logs/"today"; workspace zone for planning dates | Arch §4.8 |
| 24 | Where LLM logic lives | UX puts the translator on‑device; product owner asked for an independent, agentic LLM server | One standalone gateway on LiteLLM (P2b) serving clients and server; runs locally (`local_only`) or shared; deterministic fallbacks everywhere | P2b, ADR‑0018 |
| 24a | Agent write access | — | Read‑only tools by default; write tools per task, opt‑in (`send`/`apply`), always via the capture/overlay APIs with `agent_run_id` audit | P2b, ADR‑0019 |
| 24b | Insights provider | "LLM‑ or rule‑generated" | Gateway `summariser` alias (Anthropic default in hybrid/cloud, Ollama in local_only), flag‑gated, deterministic fallback in the core server | P7 |
| 28 | Presentation output | UX §5 describes one scrolling page | Composable deck: pick scope + sections → one slide per screen with next/prev; same template exports any report/view; one‑page scroll mode kept as a layout of the same deck | P7, ADR‑0011/0021 |
| 29 | Enterprise auth | Spec: generic OIDC | Active Directory via LDAPS (group → role mapping) and/or OIDC (Entra ID/ADFS); optional Kerberos SSO; HTTPS with PEM cert/key on Linux; internal CA bundle for clients | P4 §4.7, P12, ADR‑0022 |
| 25 | Time/plan on multi‑item lines | — | Apply to the first item unless each element carries its own | P2 |
| 26 | Approval policy source | — | `Project.settings.approval_policy`, default `any_one` | P3 |
| 30 | External LLM packs | Spec silent | Download off by default (`external_packs`), standard redaction, never wellbeing/habits/live sessions; in‑product “Generate with AI” only through the gateway | P7 §7.6 |
| 31 | Share‑link redaction | Spec silent | `standard` (no wellbeing, habits, live sessions); `strict` also pseudonymises people | P7, ADR‑0033 |
| 32 | Kerberos | NT sign‑in required | Proxy‑terminated, header trusted only from `TT_TRUSTED_PROXIES`; in‑process GSSAPI not built | P4 §4.6, ADR‑0036 |
| 33 | Rate limiting | Plan: per token | In‑process sliding window (capture per credential, sign‑in per address); cluster‑wide backoff for sign‑in in the DB | P12 §5 |
| 34 | Raw input retention | Plan: configurable | Keep by default; `raw_input_retention_days` per workspace, nightly purge | P12 §4 |
| 35 | Events partitioning | Plan: monthly | Deferred (not needed at 1 M events, see load report); revisit at 10 M | P12 §1 |
| 27 | Mandatory fields in manual mode | Product request | Client‑side only; `pack.manual_mode.required_fields`, default action/item/project | P2 §2.6 |
| 36 | Version string of a batch with commands | Plan: "schema version bump" | `CLIENT_SCHEMA_VERSION = "0.2"` sent by clients that know commands; the model default stays `"0.1"` (spec parity) | P14, ADR‑0051 |
| 37 | Client-owned audit block on a command | Plan: "event carries `client_version`, `mode`, `role_mode`" | `args["_meta"] = {client_version, mode, role_mode, forced}`; the server strips it before schema validation and records it in the event payload | P14, ADR‑0051 |
| 38 | Preview idempotency | Plan: "preview never writes" | A preview is a separate `preview_only` request whose `client_id` is `<id>:preview`, so it can never shadow the real command | P14 |
| 39 | What counts as "wide" | Plan: "bulk stage.move/item.tag over many items" | More than 5 targets (`keys`/`rows`) on `stage.move`, `item.tag`, `item.move_to_iteration`, `item.reparent`, `review.bulk` is treated as destructive by the client in addition to the catalogue flag | P14 |
| 40 | `--yes` and destructive ops | Plan: "`--yes` alone does not bypass; `--force` does and is logged" | Without a terminal and without the flags the entry stays in the outbox (exit 3); `--force` marks `_meta.forced` on exactly the commands it overrode | P14, ADR‑0052 |
| 41 | Rejected commands in the outbox | — | A rejected command is final: parked in `outbox/rejected/` with the server's errors, not retried; applied ones are dropped; commands never answered stay queued | P14, ADR‑0052 |
| 42 | Role mode persistence and offline default | Plan: "persists in config" | `role_mode` in `[client]` of `config.toml`; allowed modes cached from `/me` (`me.json`); offline or unknown = reporter only | P14 |
| 43 | Picker hints in `args_schema` | Plan: "pickers for person/project/team/item" | `format`/`x-picker` of `person|project|team|item` on a string (or its array items); property-name hints (`person`, `manager`, `lead`, `members`, `project`, `team`, `keys`, `parent`, and `key` for `item.*` ops) when absent | P14 |
| 44 | Command translator grounding | Plan: "never emits an op absent from the catalogue" | Catalogue `op` enum in the model schema; per-op JSON Schema validation; people / projects / item keys must come from the pack, tool results or the person's own words; `me` = actor; a `project.create` key grounds later commands in the same request | P14 |
| 45 | Slack destructive confirmation | Plan: "approvals and waives as message buttons" | Approve / Reject = one click; destructive Confirm carries Slack's native confirm dialog and the handler refuses a click without the `second` flag | P14 |
| 46 | Shape of review rows / team activity for the client | `GET /review-queue?mine=true`, `GET /activities?team=mine` exist | Rows `{id, summary|title, default_choice?, choices?}`; activities `{person (handle or {handle}), item {key,title}}`; a row without a default needs the web console | P14 |

## Questions for the product owner (non‑blocking; defaults above apply until answered)
0. **Raised by the 2026‑10‑24 review work:** (a) Kerberos proxy‑terminated or in‑process GSSAPI? (b) External packs: confirm download‑off / in‑product‑on. (c) Share‑token redaction default `standard` or `strict`? (d) Should admins get an audited override of the Inbox privacy rule (ADR‑0024)? (e) Is a replay‑capable event log (full‑row payloads) worth the storage (ADR‑0040)?
1. Should Mode B (manual) be allowed to bypass the local LLM entirely in environments where no model is installed, or should assisted mode be hidden there? (Default: assisted falls back to the structured parser and offers manual.)
2. Should reporters be able to see their own habits of category `wellbeing` in the client (spec: yes, §13.3) and should the client show violation pings? (Default: yes to both, pings off by default.)
3. Which Jira flavour (Cloud vs Server/DC)? (Default: Cloud.)
4. Target scale for v1 (members, items/year)? (Default budgets assume 200 members, 100 k items/year.)
5. Is OIDC provider known (Azure AD, Google, Okta)? (Default: generic OIDC.)

## Answers adopted for the open product questions (2026-10-28; the product owner can overrule any of them)
| # | question | decision adopted | status in code |
|---|---|---|---|
| 0a | Kerberos | proxy-terminated (header trusted only from `TT_TRUSTED_PROXIES`); in-process GSSAPI stays out of v1 | as built (ADR-0036) |
| 0b | External generation packs | download off, in-product (import) on | as built |
| 0c | Share-token redaction default | `standard`; `strict` selectable per workspace (`settings.external_share_redaction`) | as built |
| 0d | Admin override of the Inbox privacy rule | **no override in v1**: admins never see another person's Inbox (ADR-0024); an audited break-glass is a v2 candidate | as built |
| 0e | Replay-capable event log | **not in v1**: events carry before/after summaries, not full rows; revisit if replay or audit export is requested (ADR-0040) | as built |
| 0f | Postgres TLS / Kerberos in-process | Postgres `verify-full` supported and tested; in-process Kerberos deferred with 0a | as built |
| 14.x | Rejected management commands | logged as `command_log` rows with status `rejected`, excluded from idempotency (ADR-0056) | see leftovers/p14 |
| sec | `filelock` CVEs vs `litellm<4` cap | uv `override-dependencies = ["filelock>=4.2.1"]` in the root `pyproject.toml`; gateway suite passes with filelock 4.2.4; remove the override when litellm lifts the cap | done |

## Phase 15 defaults (2026-10-25)
| # | question | default adopted | where |
|---|---|---|---|
| 15.1 | ADR numbers 0029-0031 are taken | the Phase 15 ADRs are 0053, 0054, 0055 | `docs/decisions` |
| 15.2 | SQLCipher or Fernet | Fernet per row; key in the OS keyring, 0600 file fallback | ADR-0053 |
| 15.3 | A line with no label, keyword, tag or project | personal (private until said otherwise); a model may only make it more private, a less private reading is a question | `parser/personal.py`, `parser/personal_llm.py` |
| 15.4 | A bare time ("9-10 reading") | today, even if past; no date and no time = today, anytime | golden set |
| 15.5 | `time.unlog` without a new `ClientAction` | `action: update` + `corrects: <client_id>` (the enum is fixed by the spec) | ADR-0054 |
| 15.6 | Category of the learning policy/habit | `wellbeing` (the enum has no development category; it gives subject + line manager visibility) | ADR-0055 |
| 15.7 | Who sees the DEV project | the person, their line manager, and admins by the platform rule for private items | ADR-0055 |
| 15.8 | Client domains for suggestions | workspace setting `calendar.client_domains` {domain: project key} | `contextpack.py` |

## ADR template (`docs/decisions/ADR-NNNN-title.md`)
```
# ADR-NNNN: <title>
Date: YYYY-MM-DD   Status: proposed | accepted | superseded by ADR-MMMM
## Context
<what forced the decision; cite spec section>
## Decision
<one paragraph>
## Consequences
<what becomes easier/harder; migration notes>
## Alternatives considered
<bullets>
```
