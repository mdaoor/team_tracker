# Appendix C — Open decisions, assumptions and ADR template

The spec is silent or ambiguous in the places below. Each row records the default this plan set adopts so the implementer never stalls. Overriding a default requires an ADR.

| # | topic | spec status | default adopted | where |
|---|---|---|---|---|
| 1 | Event sourcing depth | "Event log is source of truth; tables are projections" | Transactional projection (event + table in one tx), rebuild tool in P12; no replay‑only reads | Arch §4.1, ADR‑0001 |
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
| 17 | Desktop ↔ `tt_client` bridge | — | Python sidecar (PyInstaller) in v1 | P11, ADR‑0016 |
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
| 30 | Plan‑first projects | Spec assumes items arrive from capture | Phases/deliverables are manager‑created `WorkItem`s (types `phase`/`deliverable`, `source.origin=plan`); statuses derived, never stored; every slide kind declares plan‑only behaviour; seed has a zero‑task project | P1 §1.2, P3 §3.11, P7, ADR‑0023 |
| 29 | Enterprise auth | Spec: generic OIDC | Active Directory via LDAPS (group → role mapping) and/or OIDC (Entra ID/ADFS); optional Kerberos SSO; HTTPS with PEM cert/key on Linux; internal CA bundle for clients | P4 §4.7, P12, ADR‑0022 |
| 25 | Time/plan on multi‑item lines | — | Apply to the first item unless each element carries its own | P2 |
| 26 | Approval policy source | — | `Project.settings.approval_policy`, default `any_one` | P3 |
| 27 | Mandatory fields in manual mode | Product request | Client‑side only; `pack.manual_mode.required_fields`, default action/item/project | P2 §2.6 |

## Questions for the product owner (non‑blocking; defaults above apply until answered)
1. Should Mode B (manual) be allowed to bypass the local LLM entirely in environments where no model is installed, or should assisted mode be hidden there? (Default: assisted falls back to the structured parser and offers manual.)
2. Should reporters be able to see their own habits of category `wellbeing` in the client (spec: yes, §13.3) and should the client show violation pings? (Default: yes to both, pings off by default.)
3. Which Jira flavour (Cloud vs Server/DC)? (Default: Cloud.)
4. Target scale for v1 (members, items/year)? (Default budgets assume 200 members, 100 k items/year.)
5. Is OIDC provider known (Azure AD, Google, Okta)? (Default: generic OIDC.)

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
