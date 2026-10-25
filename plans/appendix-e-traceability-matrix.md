# Appendix E — Traceability matrix (spec → phase → proof)

Keep this current. "Proof" names the test module (fill in as tests are written).

| spec | topic | phase(s) | proof |
|---|---|---|---|
| OM §0 | Two layers; overlay never edits capture rows | P0 (layer metadata), P1 (guard test), P3 | `packages/schema/tests/test_models.py::test_layers_cover_every_work_item_field` (guard test: P1) |
| OM §1.1 | Universal `WorkItem` tree | P1 | `tests/integration/items/test_tree.py` |
| OM §1.2 | Append‑only Event log, tables are projections | P0, P1, P12 (rebuild) | `apps/server/tests/integration/test_uow.py`, `test_outbox.py`, `tests/unit/test_event_catalogue.py` (P0); `apps/server/tests/api/test_acl_verify.py` (P12: `verify-projection`, replay rebuild not possible, ADR‑0040) |
| OM §1.3 | Methodology is configuration | P1 (workflows), P3 (process) | `tests/integration/projects/test_workflow.py` |
| OM §1.4 | Capture never blocks; Inbox; provisional | P1 §1.4 | `tests/api/capture/test_never_blocks.py` |
| OM §1.5, §4 | Dimensions, values, classification, mirrors | P3 §3.1–3.2 | `apps/server/tests/seeded/test_overlay_seeded.py::test_custom_dimension_lifecycle_and_classified_as`, `…::test_single_cardinality_replaces_and_manual_wins_over_inherited`, `integration/test_seed.py::test_mirrors_cover_every_item` |
| OM §1.6, §5 | Process, stages, placements, transition rules | P3 §3.3 | `seeded/test_overlay_seeded.py` (stage/state sync, board, approvals, evidence rule), `e2e/test_concurrency.py` |
| OM §1.7, §6 propagation | Propagation down, roll‑ups up | P3 §3.6 | `seeded/test_overlay_seeded.py` (priority, risk inheritance, roll-up, provenance) |
| OM §1.8, §10 | Privacy / access control | P4 | `apps/server/tests/unit/test_acl_matrix.py`, `tests/api/test_acl_{core,overlay,privacy,tokens,identity,export,perf}.py`, `unit/test_acl_router_coverage.py` |
| OM §1.9, §8 | Policies → violations → habits | P8, P9 | `tests/governance/*`, `tests/oracle/test_violations.py` |
| OM §3 | Tenancy, people, hierarchy, context notes | P1 §1.1 | `tests/integration/tenancy/*` |
| OM §6 Product/Objective/KR | Products, objectives | P3 §3.4 | `seeded/test_overlay_seeded.py` (product tree, delivers rule, objectives) |
| OM §6 Weights | Schemes, weights, value_score | P3 §3.5 | `unit/test_weights_and_approvals.py`, `seeded/test_overlay_seeded.py` (schemes, overrides, value_score) |
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
| OM §13.2 | Classification confirmation rights | P3 §3.2, P4 | `apps/server/tests/api/test_acl_overlay.py::test_classification_confirmation_is_for_the_line_manager` (weak: asserts access, not the confirm action itself) |
| OM §13.3 | Habits visible to subject | P4, P9 | `apps/server/tests/api/test_acl_privacy.py`, `test_acl_governance.py` (habits), `test_acl_policy_editors.py::test_habit_series_matches_the_stored_habit_and_respects_visibility`, `test_acl_retention.py` (export excludes private data) |
| OM §13.4 | Weight scheme per report | P3, P6, P7 | `tests/api/query/test_scheme.py` |
| OM §14 | Tier‑3 platform‑owned; shared templates copied | P8 | `tests/governance/test_templates.py` |
| UX §0 | Persona success metrics | P12 (analytics) | load/UX report |
| UX §2.1 | Form factors | P2 (CLI), P11 (desktop, mobile, chat) | e2e per shell |
| UX §2.2 | Capture palette behaviour | P2 (preview rows), P11 | `apps/desktop/src/palette/palette.test.tsx` (13), `apps/slack_bot/slack_tests/test_slack_bot.py` (13); native shell not built |
| UX §2.3 | Verb table | P2 §2.2 | `packages/client_core/tests/test_structured_parser.py` |
| UX §2.4 | Local LLM translator + ContextPack | P2 §2.1, 2.3, P2b `/v1/translate` | golden harness, `tests/api/test_context_pack.py` |
| Product req. | Independent, agentic LLM gateway (LiteLLM routing, tools, batch review, suggestions, policy authoring) | P2b | `apps/llm_gateway/gateway_tests/*` (routing/privacy/budget, 31 recorded agent scenarios, golden set through the gateway, injection in `test_translate.py` and `test_capture_agent.py`, batch review), `apps/server/tests/e2e/test_gateway_e2e.py`, `apps/server/tests/api/test_acl_llm.py`; real-model accuracy and latency not measured |
| Product req. | Slide‑based presentation builder; generic template exporting any report | P7 §7.2 | `apps/presentation/e2e/deck.spec.ts` |
| Product req. | HTTPS with PEM on Linux; Active Directory (NT accounts and groups) auth | P4 §4.6, P12 §6 | `apps/server/tests/api/test_acl_identity.py` (ldap3 in-memory AD; a real DC not covered), `test_acl_sso.py` (Kerberos behind a proxy), `tests/integration/test_tls.py`, `test_db_tls.py` (real TLS Postgres, `verify-full`), `tests/unit/test_tls_reload.py` (hot reload) |
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
| UX §7.1–7.3 | Flow walk‑throughs | P2, P5, P7 | `apps/web/e2e/walkthrough.spec.ts` (asserted; video via `TT_VIDEO=1`, not produced in the authoring sandbox: ffmpeg build mismatch) |
| UX §8 | Build order | README phase map | — |
| gen_snapshot.py | Snapshot shape & oracle | P0 (seed), P7, P8 | `tests/oracle/*` |

## Added by review packages A–I and phases 11–12 (2026‑10‑24)
| spec / plan | topic | phase(s) | proof |
|---|---|---|---|
| Review 1–7 | Security fixes (deck XSS, token ceiling, review ownership, role tenancy, StartTLS, PPTX empty, truncation) | A | `tests/api/test_review_a.py`, `tests/unit/test_ldap_starttls.py` |
| Review 8–23 + Low | ACL completeness, robustness | B | `tests/api/test_review_b.py`, `tests/unit/test_habit_team_trend.py`, `apps/server/tests/review/TESTPLAN.md` |
| Review §4 | Deviations recorded | C | ADR‑0016 … ADR‑0037 (`docs/decisions/`) |
| P1 §1.2 | Top‑down plan API, derived statuses | D | `tests/api/test_acl_plan.py`, `tests/unit/test_plan_status.py`, `docs/api/plan.md` |
| P3 §3.11 | Task ↔ deliverable linkage, roll‑ups, `ERP` plan‑only seed | E | `tests/api/test_acl_plan.py`, `tests/seeded/test_plan_only_seeded.py` |
| P7 plan kinds, plan‑only rule | `roadmap` … `project_overview`, `requires`/`render_empty` | F | `tests/seeded/test_plan_deck_seeded.py`, `tests/unit/test_slide_kind_meta.py`, `apps/presentation/src/renderers.test.js` |
| P7 §7.5 | Templates as data | G | `tests/api/test_acl_presentation_templates.py`, `apps/web/e2e/templates.spec.ts`, ADR‑0037 |
| P7 §7.6, P2b | External generation pack, import, author tasks, “Generate with AI” | H | `tests/api/test_acl_generation_pack.py`, `apps/llm_gateway/gateway_tests/test_presentation_author.py`, `apps/web/e2e/present.spec.ts` |
| P1 scope | Comments, checklists, links, custom fields, attachments (local + S3), recurrence, progress heal | I | `tests/api/test_acl_item_extras.py`, `test_acl_recurrence.py` |
| P5/P9 | Roadmap editor, Present this plan, Add to presentation, policy editors, version history, habit sparklines | I | `apps/web/e2e/plan.spec.ts`, `governance.spec.ts`, `tests/api/test_acl_policy_editors.py`, `apps/web/src/lib/{plan,governance}.test.ts` |
| P5/P9 acceptance | Accessibility (axe ≥ 95), component stories | I | `apps/web/e2e/a11y.spec.ts` (100 on 14 screens), `apps/web/src/stories/stories.test.tsx` |
| P2b/P7 | Contract tests; real‑model goldens | I | `apps/server/tests/contract/test_schemathesis.py`; `gateway_tests/test_real_model_golden.py` (opt‑in, not run) |
| P4 §4.6 | Kerberos (proxy), Postgres verify‑full, cert reload | I | see Product req. row above; ADR‑0036 |
| P11 | Slack, desktop palette | 11 | see UX §2.2 row; ADR‑0038, ADR‑0039 |
| P12 §1 | Load fixture and budgets | 12 | `tools/seed/load.py`, `tools/perf_report.py`, `docs/ops/load-report.md` |
| P12 §2 | Metrics, dashboards, alerts | 12 | `tests/api/test_acl_hardening.py`, `ops/` |
| P12 §4 | Retention, export, erase, verify | 12 | `tests/api/test_acl_retention.py`, `test_acl_verify.py` |
| P12 §5 | Rate limits, CSP/headers, threat model | 12 | `tests/api/test_acl_hardening.py`, `docs/security/threat-model.md` |
| P12 §6–7 | Deployment artefacts, config reference, docs | 12 | `tests/unit/test_config_reference.py`, `deploy/`, `docs/ops/`, `docs/runbooks/`, `docs/guides/` |


## Phase 15 (2026-10-25)
| spec / plan | topic | phase(s) | proof |
|---|---|---|---|
| Product req. | Personal time tracker, optional send, Google/Outlook/Teams meetings as ready-to-send tasks, simple send | 15 | `packages/client_core/tests/test_personal_*.py`, `test_calendar_*.py`, `apps/cli/tests/test_personal_cli.py`, `apps/server/tests/api/test_personal_send.py`, `apps/desktop/src/personal/*.test.tsx`, `apps/desktop/e2e/personal.spec.ts`; real accounts, OS keyrings and Tauri shells not covered |
| P15 §15.1 | Local store, model, timer, export/erase, RRULE across DST | 15 | `test_personal_store.py`, `test_personal_recurrence.py` (Hypothesis), ADR-0053 |
| P15 §15.2 | Day/week/month, tray, end-of-day and weekly cards, quick add (>= 60 golden lines) | 15 | `test_personal_tracker.py`, `test_personal_parser.py` + `golden/personal.yaml`, desktop tests, `docs/spec/team_tracker_ux_addendum_personal.md` |
| P15 §15.3 | Calendar protocol, Google, Microsoft/Teams, sync scheduler, token storage | 15 | `test_calendar_providers.py`, `test_calendar_sync.py`, `docs/client/calendar-integrations.md` |
| P15 §15.4 | `calendar_ref` dedup, `time.unlog`, DEV project ACL, ContextPack additions, `development_hours_below` | 15 | `test_personal_send.py`, `test_personal_governance.py`, migration 0014, ADR-0054/0043 |
| P15 §15.5 | CLI commands, desktop views through the sidecar | 15 | `apps/cli/tests/test_personal_cli.py`, `test_personal_bridge.py` + `contract.json` |
| P15 §15.6 | Privacy: first run, toggles, audit, unsend, erase, tokens never in outbox/logs | 15 | `test_personal_tracker.py::test_tokens_never_*`, desktop privacy tests |
| P2 §2.2 / P15 | Gateway translate variant for personal lines (local only, sends nothing) | 15 | `apps/llm_gateway/gateway_tests/test_translate_personal.py` |
