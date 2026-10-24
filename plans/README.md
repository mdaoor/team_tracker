# Team Tracker — Implementation Plan Set (handover edition)

This folder is the complete, self-contained brief for an implementing agent (LLM or human) to build Team Tracker from the design documents in `docs/spec/`. Nothing outside this repository is required to start.

## What Team Tracker is (one paragraph)

A work-tracking system with **two layers**. The *capture layer* lets any reporter type one plain-English line ("fix the login redirect loop, took 2h, plan to finish tomorrow") into a client; a local LLM turns it into a structured `ClientBatch`; the server records a `WorkItem`, `Activity`, `TimeEntry`, `Evidence` and never blocks or asks questions. The *management overlay* lets many managers independently enrich those items server-side — project, product, delivery stage, priority, weight, risk, people, arbitrary dimensions — by writing rows that *point at* the capture rows and never rewrite them. An append-only `Event` log is the source of truth; everything is rendered as views (list, board, pipeline, matrix, gantt, now, summary) and governed by a CEL-based Policy → Violation → Habit engine. See `docs/spec/team_tracker_object_model.md` §0 and §1.

## Source documents (read in this order)

| file | what it is | authority |
|---|---|---|
| `docs/spec/team_tracker_object_model.md` | Object model v0.4: entities, fields, relationships, propagation rules, governance design, resolved decisions | **Normative.** When a plan and this document disagree, the object model wins unless the plan explicitly says it is overriding and why. |
| `docs/spec/team_tracker_models.py` | Python dataclasses + enums mirroring the object model; `ClientMessage` / `ClientBatch` contract; `to_json_schema()` | **Normative for field names, enum values and defaults.** Port it; do not redesign it. |
| `docs/spec/team_tracker_ux.md` | UX design v0.1: personas, reporter palette, ContextPack, manager console, views, presentation page, flows, build order | **Normative for behaviour**; layout details are guidance. |
| `docs/spec/team_tracker_presentation.html` | Working prototype of the presentation page with an embedded sample snapshot | **Reference implementation** of the snapshot JSON shape and the rendering logic. Phase 7 turns it into the real page. |
| `tools/gen_snapshot.py` | Generates the sample snapshot JSON used by the prototype | Use as the fixture generator for tests and demos. |
| `plans/00-architecture-and-stack.md` | Technology decisions and repo layout for this implementation | Normative for *how* we build. |

The original design conversation that produced these documents is not reproduced here; everything decided in it is captured in the documents above and in `plans/appendix-c-open-decisions-and-assumptions.md`.

Four requirements were added by the product owner during planning and are **not** in the spec documents; the plans treat them as normative:

| requirement | where it is planned |
|---|---|
| Two client sending modes: **assisted** (free text → LLM → a JSON file the reporter owns → server, optionally Jira) and **manual** (import context pack → form with mandatory fields, tags, project, time, …) | Phase 2 §2.6, Phase 11, Phase 10 |
| A **separate LLM server** on LiteLLM with **agentic** calls that take the local tasks and route them to the model; LLM logic independent of client and core server | Phase 2b (`14-phase-2b-llm-gateway.md`) |
| Presentation as a **deck**: choose what to present, one slide per screen, next/previous; a **generic template** that can export a presentation from any report or view | Phase 7 |
| **HTTPS with PEM certificates on Linux**; sign‑in with **Windows NT (Active Directory) accounts and NT groups** | Phase 4 §4.6, Phase 5 (IdP panel), Phase 12 (deployment) |
| **Top‑down planning**: a manager builds projects with phases, deliverables and milestones with dates, and can present all of it as a deck even if nobody has logged a task; when tasks exist the same slides add the detail | Phase 1 §1.2 (plan API, derived status), Phase 3 §3.11 (task ↔ deliverable linkage, roll‑ups, plan‑only seed project), Phase 5 (roadmap editor, "Present this plan"), Phase 7 (plan slide kinds, plan‑only rule, "Project plan review" template) |

## Addenda to phases that are already implemented

Phases 0–4 were implemented (PR mdaoor/team_tracker#2, merged into this branch) before the top‑down‑planning requirement was added. The following plan sections therefore describe work that is **not yet built** and must be picked up as a follow‑up before or alongside Phase 5:

| section | missing in the implementation |
|---|---|
| Phase 1 §1.2 "Top‑down project plan" | `POST/GET /projects/{key}/plan`, derived plan statuses for deliverables/milestones/projects, `Milestone.kind` in the plan read |
| Phase 3 §3.11 "Plan ↔ execution linkage" | task → deliverable auto‑parenting, `ReviewItem{kind=unlinked_task}`, plan‑tree roll‑ups, dependency re‑evaluation |
| Phase 3 scope item 13 | seed project `ERP` (plan‑only, zero tasks) in `tools/seed/demo.py` |

Track them as the first tasks of the Phase 5 branch (they are server‑side prerequisites of the roadmap editor) and tick them off here when done.

## How to use these plans

1. Read `README.md` (this file), then `00-architecture-and-stack.md`, then the appendices A–C once.
2. Execute phases **in order** unless the dependency graph below says a phase can run in parallel.
3. Each phase file has the same shape: *Goal · Depends on · Inputs · Scope / Out of scope · Deliverables · Detailed tasks · Data & API contracts · Acceptance criteria · Exit checklist · Handover notes*. Treat the acceptance criteria as the test plan; a phase is not done until every checkbox in its exit checklist is true and the commands in `appendix-b-testing-and-dod.md` pass.
4. When the implementer hits a decision that the spec does not settle, follow `appendix-c-open-decisions-and-assumptions.md`. If still unresolved, pick the simplest option that keeps the capture layer non-blocking, record it in `docs/decisions/ADR-NNNN-*.md` (template in appendix C) and continue. Never stop a phase on an unresolved product question.
5. Use `appendix-d-handover-prompt-templates.md` to brief a fresh agent on a single phase.
6. Keep `appendix-e-traceability-matrix.md` current: every spec section must map to the phase that implements it and to the tests that prove it.

## Phase map and dependency graph

```
P0 Bootstrap ─► P1 Core domain + capture ingestion ─► P2 Reporter CLI, ContextPack, translator client
                         │                                      │ ◄──► P2b LLM gateway (LiteLLM, agentic; parallel with P2)
                         │                                      │
                         ├─► P3 Management overlay ─► P4 Access control ─► P5 Web shell + Manager console
                         │                                      │                     │
                         │                                      └─► P6 Query API + Views ─► P7 Snapshot API + Presentation + Insights
                         │                                                              │
                         └─────────────────────────────────────────► P8 Governance engine ─► P9 Governance UI + Habits
P2b LLM gateway      (parallel with P2; consumed by P5, P7, P9, P11)
P10 Jira sync        (parallel, after P3)
P11 Desktop / mobile / chat clients (parallel, after P2)
P12 Hardening & release (last)
```

| # | file | title | rough size* |
|---|---|---|---|
| 0 | `01-phase-0-bootstrap.md` | Repo, tooling, CI, schema package, migrations, seed | S |
| 1 | `02-phase-1-core-domain-and-capture.md` | Tenancy, projects, work items, Event log, `ClientBatch` ingestion, activities, time, evidence, daily log | L |
| 2 | `03-phase-2-reporter-cli-contextpack-translator.md` | ContextPack endpoint, regex parser, CLI, two sending modes, translator client, offline queue | M |
| 2b | `14-phase-2b-llm-gateway.md` | Standalone LLM gateway on LiteLLM: routing, privacy modes, agentic capture with tools, summaries, suggestions, policy authoring | L |
| 3 | `04-phase-3-management-overlay.md` | Dimensions, classifications, process & stages, products, objectives, weights, propagation, risks, review queue API | L |
| 4 | `05-phase-4-access-control.md` | Roles, grants, visibility, reporting-line rule, wellbeing rule, scope filtering; Active Directory (NT accounts/groups) sign‑in and group → role mapping; HTTPS with PEM certificates | L |
| 5 | `06-phase-5-web-shell-and-manager-console.md` | React app shell, auth, setup editors, review queue, translator rules | L |
| 6 | `07-phase-6-query-api-and-views.md` | SavedView filter grammar → SQL, list/board/pipeline/matrix/now/gantt/calendar | L |
| 7 | `08-phase-7-snapshot-presentation-insights.md` | `/api/snapshot`, presentation builder (choose scope + sections → deck, one slide per screen, next/prev), generic slide template with "add any report as a slide", HTML/PDF/PPTX export, page mode = prototype, LLM summaries (`Insight`) | L |
| 8 | `09-phase-8-governance-engine.md` | CEL evaluator, policy context schema, templates, Tier‑3 functions, scheduler, violations, habits, metric snapshots | L |
| 9 | `10-phase-9-governance-ui-habits.md` | Policy editor + dry‑run, violations inbox, habit report, insights review | M |
| 10 | `11-phase-10-jira-sync.md` | Outbound projection to Jira, inbound webhooks, field mapping | M |
| 11 | `12-phase-11-desktop-mobile-chat-clients.md` | Desktop palette (Tauri), Slack/Teams bot, mobile shell | M |
| 12 | `13-phase-12-hardening-and-release.md` | Performance, observability, backups, security review, deployment, docs | M |

\* S ≈ 1–2 agent-days, M ≈ 3–5, L ≈ 6–10. These are planning aids, not commitments.

## Appendices

- `appendix-a-api-conventions.md` — URL shape, pagination, errors, idempotency, auth headers, versioning, event naming.
- `appendix-b-testing-and-dod.md` — test pyramid, fixtures, the exact commands that must pass, Definition of Done.
- `appendix-c-open-decisions-and-assumptions.md` — every place the spec is silent and the default we chose, plus the ADR template.
- `appendix-d-handover-prompt-templates.md` — copy‑paste briefs for starting a fresh agent on a phase.
- `appendix-e-traceability-matrix.md` — spec section → phase → tests.

## Non‑negotiable rules for every phase

1. **Capture never blocks.** No validation error may be returned to a reporter for missing project, unknown person, unknown tag, missing estimate. Route to Inbox / mark `is_provisional` / leave null and move on.
2. **Overlay never rewrites capture rows.** Managers write `Classification`, `StagePlacement`, `Weight`, `RiskLink`, etc. The only `WorkItem` columns managers may update are the ones marked *overlay* in object model §7.
3. **Every mutation writes an `Event` in the same transaction**, with the correct `source` (`client | overlay | propagation | rule | import`).
4. **Field names and enum values come from `team_tracker_models.py`.** Do not rename, do not add synonyms.
5. **Privacy from day one.** Every read endpoint applies the ACL filter from Phase 4 once it exists; before that, endpoints are workspace‑scoped and marked `# TODO(P4-ACL)`.
6. **Tests ship with the code.** No phase closes with coverage below the floor in appendix B.
7. **Small, reviewable commits**, conventional‑commit messages, one phase per branch (`phase/N-short-name`), merged by PR into `main`.
