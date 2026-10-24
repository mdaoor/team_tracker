# Test plan: Phase 1 §1.2 top-down plan (review package D)

| Criterion | Test |
|---|---|
| plan created in one transaction with `plan.created` | tests/api/test_acl_plan.py::test_a_plan_is_created_in_one_transaction_with_an_event |
| zero capture rows → full tree, not_started, null roll-ups | ::test_a_project_planned_with_no_work_returns_a_full_tree |
| statuses flip with dates and tasks | ::test_statuses_follow_dates_and_work |
| milestone statuses + deliverables_done/total, kind in API | ::test_milestones_carry_kind_status_and_counts |
| dependencies, at_risk from an overdue predecessor | ::test_a_late_predecessor_puts_its_successor_at_risk |
| baseline = approval, variance in the read | ::test_baseline_variance_is_in_the_read |
| invalid plan is atomic | ::test_a_bad_plan_creates_nothing |
| ACL | ::test_plan_endpoints_are_permission_gated |
| derived-status functions | tests/unit/test_plan_status.py |
