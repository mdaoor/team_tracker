# Top-down project plan (plan P1 §1.2, P3 §3.11)

A project can be planned before anyone logs a task. `POST /projects/{key}/plan` creates, in one transaction, `phase` items →
`deliverable` items → `Milestone` rows → `finish_to_start` links, and emits one `plan.created` event.

```json
{"phases": [{"title": "Foundation", "planned_start": "2026-09-01", "planned_end": "2026-11-15",
             "deliverables": [{"ref": "gov", "title": "Governance and scope", "planned_end": "2026-09-30", "milestone": "Kick-off complete", "owner": "@sara"},
                              {"title": "Infrastructure", "depends_on": ["gov"], "lag_days": 2}]}],
 "milestones": [{"name": "Kick-off complete", "kind": "gate", "due_date": "2026-09-30"}]}
```
A bad reference (unknown owner / milestone / `depends_on`, `planned_end` before `planned_start`) returns 400 with a pointer and creates nothing.

`GET /projects/{key}/plan?as_of=YYYY-MM-DD` returns the tree. Statuses are **derived on read** (no column):

| object | status | rule |
|---|---|---|
| deliverable | `done` | state category is done |
| | `overdue` | `planned_end` before today and not done |
| | `at_risk` | `planned_end` within 7 days and progress < 50 % (no tasks counts as 0), or a `finish_to_start` predecessor is overdue |
| | `in_progress` | any task under it, or any activity |
| | `not_started` | otherwise ("no activity reported yet") |
| milestone | `completed` / `overdue` / `due_soon` (≤ 14 days) / `planned`, plus `deliverables_done` / `deliverables_total` |
| project | `health`: `red` if a milestone is overdue; `amber` for a due-soon milestone or an at-risk/overdue deliverable; `green`; `unknown` with no plan. `planned_start/end` default to the min/max of the phases |

Roll-ups: a deliverable's `progress_pct` is the share of its tasks that are done, **null while it has none**; a phase sums its deliverables'
tasks. `progress_mode=children` is the default for `phase` and `deliverable` items and the stored `progress_pct` is refreshed when a task
finishes. Each deliverable also carries `linked_tasks`, `hours_logged`, `last_activity_at`, `depends_on` and `baseline_variance_days`
(current `planned_end` minus the latest baseline's; `POST /projects/{key}/baselines?name=` is plan approval).

## Linking captured tasks to deliverables
When capture creates a task in a project (not an Inbox), `domain/plan/linkage.py` resolves the parent in this order:
LLM hint `parent` (existing capture hint, ≥ 0.7) → glossary phrase naming a deliverable → trigram score ≥ 0.5 against deliverable titles
(best of `similarity`, and `word_similarity` both ways) → otherwise a `ReviewItem{kind=unlinked_task}` with the top-3 deliverables
(`choice: {"deliverable": "ERP-3"}` links it, `ignore` keeps it unlinked). The decision is reported as `resolution.plan_link`
(`linked:<key>`, `asked`, `no_plan`, `has_parent`, `container`).

Moving a deliverable's dates (`PATCH /items/{key}` with `planned_end`, or the capture `plan` action) raises, once per item, a
`plan_slip` review row (later than the baseline by more than `workspace.settings.plan_slip_days`, default 7) and a `plan_conflict` row
on successors that no longer fit `predecessor.planned_end + lag`. The P8 `plan_slip` policy template can replace the rows later.
