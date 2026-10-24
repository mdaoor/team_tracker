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
| B* | tests/api/test_review_b.py |
