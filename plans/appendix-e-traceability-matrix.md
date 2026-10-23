# Appendix E — Traceability matrix (spec → phase → proof)

Keep this current. "Proof" names the test module (fill in as tests are written).

| spec | topic | phase(s) | proof |
|---|---|---|---|
| OM §0 | Two layers; overlay never edits capture rows | P0 (layer metadata), P1 (guard test), P3 | `packages/schema/tests/test_models.py::test_layers_cover_every_work_item_field` (guard test: P1) |
| OM §1.1 | Universal `WorkItem` tree | P1 | `tests/integration/items/test_tree.py` |
| OM §1.2 | Append‑only Event log, tables are projections | P0, P1, P12 (rebuild) | `apps/server/tests/integration/test_uow.py`, `test_outbox.py`, `tests/unit/test_event_catalogue.py` (P0); `tests/oracle/test_rebuild.py` (P12) |
| OM §1.3 | Methodology is configuration | P1 (workflows), P3 (process) | `tests/integration/projects/test_workflow.py` |
| OM §1.4 | Capture never blocks; Inbox; provisional | P1 §1.4 | `tests/api/capture/test_never_blocks.py` |
| OM §1.5, §4 | Dimensions, values, classification, mirrors | P3 §3.1–3.2 | `tests/integration/dimensions/*` |
| OM §1.6, §5 | Process, stages, placements, transition rules | P3 §3.3 | `tests/integration/process/*` |
| OM §1.7, §6 propagation | Propagation down, roll‑ups up | P3 §3.6 | `tests/integration/propagation/*` |
| OM §1.8, §10 | Privacy / access control | P4 | `tests/acl/*` |
| OM §1.9, §8 | Policies → violations → habits | P8, P9 | `tests/governance/*`, `tests/oracle/test_violations.py` |
| OM §3 | Tenancy, people, hierarchy, context notes | P1 §1.1 | `tests/integration/tenancy/*` |
| OM §6 Product/Objective/KR | Products, objectives | P3 §3.4 | `tests/integration/products/*` |
| OM §6 Weights | Schemes, weights, value_score | P3 §3.5 | `tests/integration/weights/*` |
| OM §7 WorkItem fields | Full field list, layer tags | P0, P1 | `packages/schema/tests/test_parity.py` |
| OM §7 capture tables | Activity, Evidence, TimeEntry, DailyLog | P1 §1.4–1.5 | `tests/integration/capture/*` |
| OM §7 Event.source | Source distinguishes layers | P0/P1 | `tests/unit/test_event_catalogue.py` |
| OM §8 Policy authoring (3 tiers) | Templates, CEL, registered functions | P8 §8.1–8.3 | `packages/cel_policies/tests/*` |
| OM §8 Evaluation model | on_event / scheduled + candidate_filter | P8 §8.4 | `tests/governance/test_runtime.py` |
| OM §8 Violation | Lifecycle, waive | P8, P9 | `tests/governance/test_violations.py` |
| OM §8 Habit + detectors | Nightly habits | P8 §8.6 | `tests/governance/test_habits.py` |
| OM §8 MetricSnapshot | Metrics list | P8 §8.5 | `tests/governance/test_metrics.py` |
| OM §8 Insight | Narrative insights | P7 §7.3, P9 | `tests/integration/insights/*` |
| OM §9 SavedView filter grammar | Grammar → SQL | P6 §6.1 | `packages/filter_grammar/tests/*` |
| OM §9 layouts | pipeline, matrix, board, gantt, calendar, now | P6 §6.3–6.4 | `apps/web/e2e/views.spec.ts` |
| OM §9 derived views | Violations inbox, habit report, team performance, digest | P9 | `apps/web/e2e/governance.spec.ts` |
| OM §11 | Relationship cardinalities | P0 (constraints) | `tests/integration/test_constraints.py` |
| OM §12 | Client contract & server sequence | P1 §1.4, P2, P3 §3.12 | `tests/api/capture/test_sequence.py` |
| OM §13.1 | Stage ↔ state sync both directions | P3 §3.3 step 5 | `tests/integration/process/test_sync.py` |
| OM §13.2 | Classification confirmation rights | P3 §3.2, P4 | `tests/acl/test_confirm.py` |
| OM §13.3 | Habits visible to subject | P4, P9 | `tests/acl/test_habits.py` |
| OM §13.4 | Weight scheme per report | P3, P6, P7 | `tests/api/query/test_scheme.py` |
| OM §14 | Tier‑3 platform‑owned; shared templates copied | P8 | `tests/governance/test_templates.py` |
| UX §0 | Persona success metrics | P12 (analytics) | load/UX report |
| UX §2.1 | Form factors | P2 (CLI), P11 (desktop, mobile, chat) | e2e per shell |
| UX §2.2 | Capture palette behaviour | P2 (preview rows), P11 | `apps/desktop/e2e/palette.spec.ts` |
| UX §2.3 | Verb table | P2 §2.2 | `packages/client_core/tests/test_structured_parser.py` |
| UX §2.4 | Local LLM translator + ContextPack | P2 §2.1, 2.3, P2b `/v1/translate` | golden harness, `tests/api/test_context_pack.py` |
| Product req. | Independent, agentic LLM gateway (LiteLLM routing, tools, batch review, suggestions, policy authoring) | P2b | `apps/llm_gateway/tests/*`, injection suite, replay goldens |
| Product req. | Slide‑based presentation builder; generic template exporting any report | P7 §7.2 | `apps/presentation/e2e/deck.spec.ts` |
| Product req. | HTTPS with PEM on Linux; Active Directory (NT accounts and groups) auth | P4 §4.7, P12 §6 | `tests/acl/test_ldap_groups.py`, TLS smoke test |
| UX §2.5 | Jira hand‑off | P2 §2.6 (flag), P10 | `tests/integrations/jira/*` |
| UX §2.6 | Micro‑interactions, end of day | P2, P11, P1 §1.5 | e2e |
| Product req. | Two client modes (assisted JSON file / manual form) | P2 §2.6, P11 | `apps/cli/tests/test_modes.py`, desktop e2e |
| UX §3.1 | Console IA | P5, P9 | e2e |
| UX §3.2 | Review queue | P3 §3.8 (API), P5 §5.3 (UI) | `tests/integration/review/*`, e2e |
| UX §3.3 | Context editors | P5 §5.2 | e2e |
| UX §3.4 | Many managers, no collisions | P3 §3.10, P5 §5.1 | `tests/api/test_concurrency.py` |
| UX §4.1–4.3 | Global controls, layouts, drill‑down | P6 | e2e |
| UX §5 | Presentation page (deck + one‑page modes) | P7 | `apps/presentation/e2e/*` |
| UX §6 | Privacy in the UI | P4, P5, P6, P7, P9 | ACL e2e |
| UX §7.1–7.3 | Flow walk‑throughs | P2, P5, P7 | e2e videos (P12) |
| UX §8 | Build order | README phase map | — |
| gen_snapshot.py | Snapshot shape & oracle | P0 (seed), P7, P8 | `tests/oracle/*` |
