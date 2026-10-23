# Appendix D — Handover prompt templates

Use these to brief a fresh agent. Replace `<N>` and the bracketed parts. Keep the agent on **one phase per session**; a new session per phase avoids context drift.

## D.1 Kick‑off prompt for a phase
```
You are implementing Phase <N> of Team Tracker in the repository at <path/URL>, branch `phase/<N>-<slug>` (create from `main`).

Read, in this order, before writing code:
1. plans/README.md
2. plans/00-architecture-and-stack.md
3. plans/appendix-a-api-conventions.md, appendix-b-testing-and-dod.md, appendix-c-open-decisions-and-assumptions.md
4. plans/<phase file>  ← your task
5. The spec sections it cites in docs/spec/ (object model, UX, models.py). The object model is normative; models.py is normative for names/enums; the UX doc is normative for behaviour.
6. The "Handover notes" section of the previous phase file and docs/decisions/*.md.

Rules you must not break: the seven "Non-negotiable rules" in plans/README.md.

Work method:
- Start by writing the test list derived from the phase's Acceptance criteria into apps/server/tests/<phase>/TESTPLAN.md (or the web equivalent), then implement.
- Commit small, conventional-commit messages, run `make check` before each push.
- When the spec is silent, apply appendix C; if still unresolved, write an ADR using the template and continue. Do not stop to ask product questions; list them in the final report.
- Update docs/api/event-catalogue.md, docs/api/openapi.json, and plans/appendix-e-traceability-matrix.md as you go.

Done means: every acceptance criterion checked with a linked test, exit checklist complete, `make check` and `make e2e` green in CI, PR opened against main with the phase file linked and a summary of ADRs added and open questions.

Report back with: what was built (by module), test results (counts, coverage), deviations from the plan and why, ADRs written, open questions, and handover notes for Phase <N+1>.
```

## D.2 Resume prompt (agent continuing a partially done phase)
```
Phase <N> is partially implemented on branch `phase/<N>-<slug>`. Read plans/<phase file> and run `make check` to see the current state. Compare the acceptance criteria against existing tests (grep for `test_` names referenced in TESTPLAN.md). Finish the unchecked criteria in order of dependency, do not refactor completed work unless a test is red, and keep the commit history linear. Finish with the same report as D.1.
```

## D.3 Review prompt (independent reviewer for a phase PR)
```
Review PR <link> implementing Phase <N> against plans/<phase file> and the spec sections it cites. Check: (1) every acceptance criterion has a test that would fail if the behaviour regressed; (2) the seven non-negotiable rules in plans/README.md hold (especially capture-never-blocks and overlay-never-rewrites-capture, which have static guard tests); (3) field names/enums match docs/spec/team_tracker_models.py; (4) all emitted events are catalogued; (5) ACL applied on every new read path (phases ≥ 4). Report findings ranked by severity with file:line, and a verdict: merge / fix-then-merge / rework.
```

## D.4 Spec‑change prompt
```
A change to the design is requested: <describe>. Update docs/spec/* only via a new ADR in docs/decisions/ that states the change and the affected sections; then update the affected plans/*.md files and appendix-e. Do not edit code in this session.
```
