# Test plan: review 2026-10-24, package A (security) and B (completeness)

| Finding | Test (file::name) |
|---|---|
| A1 deck-shell XSS | tests/api/test_review_a.py::test_deck_shell_rejects_and_encodes_hostile_segments |
| A2 token ceiling | tests/api/test_review_a.py::test_a_token_cannot_mint_a_wider_token |
| A3 review resolve ownership | tests/api/test_review_a.py::test_review_resolve_needs_ownership_and_visibility |
| A4 role-assignment tenancy | tests/api/test_review_a.py::test_role_assignments_are_workspace_scoped |
| A5 StartTLS | tests/unit/test_ldap_starttls.py |
| A6 PPTX empty | tests/api/test_review_a.py::test_pptx_with_empty_data_renders |
| A7 truncation | tests/api/test_review_a.py::test_layouts_and_csv_cover_more_than_one_page |
| B8 share-token ACL | tests/api/test_review_b.py::test_share_links_never_carry_wellbeing_habits_or_live_sessions, ::test_strict_sharing_pseudonymises_people |
| B9 insight slides | tests/api/test_review_b.py::test_insight_slides_follow_project_visibility |
| B10 notification recipients | tests/api/test_review_b.py::test_role_notifications_stay_inside_their_workspace |
| B11 metric snapshots | tests/api/test_review_b.py::test_metric_snapshots_are_manager_only_for_aggregates |
| B12 capture by key | tests/api/test_review_b.py::test_capture_cannot_reach_an_item_the_reporter_cannot_see |
| B13 gateway budget | apps/llm_gateway/gateway_tests/test_service.py::test_a_workspace_token_cannot_charge_another_workspace |
| B14 CEL budget / ReDoS | packages/cel_policies/tests/test_cel.py::test_matches_rejects_patterns_that_can_backtrack_without_bound, ::test_on_event_evaluation_cpu_budget |
| B15 IdP sync | tests/api/test_review_b.py::test_one_provider_sync_never_removes_another_providers_roles |
| B16 session secret | tests/api/test_review_b.py::test_production_refuses_a_weak_or_default_session_secret |
| B17 product / roll-up visibility | tests/api/test_review_b.py::test_private_products_and_their_links_follow_visibility, ::test_rollups_apply_the_viewers_visibility |
| B18 deck `?src=` / image URL | tests/api/test_review_b.py::test_image_slides_refuse_external_hosts; apps/presentation/e2e (src allow-list) |
| B19 exports in request | tests/api/test_review_b.py::test_browser_renders_are_bounded_and_the_overflow_is_told_to_retry |
| B20 allowed_roles + propagation | tests/api/test_review_b.py::test_allowed_roles_gate_a_stage_transition_for_people_but_not_for_admins, ::test_project_visibility_and_process_changes_propagate |
| B21 agent timeout | packages/client_core/tests/test_pack_llm_form.py::test_agent_mode_gets_its_own_longer_timeout |
| B22 hot-path indexes | tests/api/test_review_b.py::test_every_declared_hot_path_index_exists_in_the_migrated_database |
| B23 team trend, lockout | tests/unit/test_habit_team_trend.py, tests/api/test_review_b.py::test_a_failed_sign_in_backs_off_per_address_and_user |
| Low: 400 not 500, bounded exports, duplicate client id | tests/api/test_review_b.py::test_bad_enum_cursor_and_size_inputs_are_400_not_500, ::test_browser_exports_are_bounded_and_say_so, ::test_a_concurrent_duplicate_client_id_is_deduplicated_not_failed |

# Packages D-I and phases 11-12 (test files; every one is also in the traceability matrix)
| Package | Tests |
|---|---|
| D plan API / E linkage + ERP seed | tests/api/test_acl_plan.py, tests/unit/test_plan_status.py, tests/seeded/test_plan_only_seeded.py (see tests/plan/TESTPLAN.md) |
| F plan slides | tests/seeded/test_plan_deck_seeded.py, tests/unit/test_slide_kind_meta.py, apps/presentation renderers tests |
| G templates as data | tests/api/test_acl_presentation_templates.py, apps/web/e2e/templates.spec.ts |
| H generation pack / import / author | tests/api/test_acl_generation_pack.py (incl. echo round trip), gateway_tests/test_presentation_author.py, e2e/present.spec.ts |
| I items, recurrence, S3 | tests/api/test_acl_item_extras.py, tests/api/test_acl_recurrence.py |
| I governance editors | tests/api/test_acl_policy_editors.py (versions, series, hostile inputs), e2e/governance.spec.ts |
| I a11y, stories, contract | e2e/a11y.spec.ts, src/stories/stories.test.tsx, tests/contract/test_schemathesis.py |
| I Kerberos / DB TLS / reload | tests/api/test_acl_sso.py, tests/integration/test_db_tls.py, tests/unit/test_tls_reload.py |
| 11 shells | apps/slack_bot/slack_tests, apps/desktop/src/palette/palette.test.tsx |
| 12 hardening | tests/api/test_acl_hardening.py, test_acl_retention.py, test_acl_verify.py, tests/unit/test_config_reference.py |
