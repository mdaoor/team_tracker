# ADR-0050: Merging items re-points capture rows by reference
Date: 2026-10-25   Status: accepted (Phase 14; plan ADR-0026)

## Context
Rule 2 says capture rows (activities, time, evidence, comments) are append-only facts about what a person reported. `item.merge`
must still make two duplicates one item without losing minutes or evidence.

## Decision
`domain/items/merge.py` moves the rows of the merged-away items to the surviving target by updating **only the foreign key**
(`work_item_id`, or `target_id` for comments/attachments). Row identity, timestamps, author and content are untouched, so the facts
stay the facts; only the item they hang off changes. Tags, links, assignments, risk links and classifications are merged without
duplicates (a manual classification beats an inherited one; the target's own row wins ties). Sources are soft-deleted with
`external_refs.merged_into = <target id>` and `item.merged` carries the counts; children of a source are re-parented to the target.

`plan()` computes the effect first (the `preview` of the command) and `apply()` executes exactly that plan, so preview and result
agree by construction. Cross-project merges need `allow_cross_project` and `project.write` on every source.

## Consequences
Total minutes and evidence count are invariant under merge (property test). History is reconstructable from `item.merged` plus the
unchanged rows. The Jira projection sees the source issues as deleted locally; pushing a "merged into" comment to Jira is left to the
Phase 10 projection when it is enabled for the project (open: not done automatically in this phase).

## Amendment (PR #11 review M6): the Jira projection announces a merge
When the source item's project has the Jira projection enabled and the source has an issue, the `jira.on_event` job reacting to the
source's `item.merged` event (`payload.after.merged_into`) does two things, in this order: (1) it first pushes the target if it is
opted in and has no issue yet, then links the source issue to the target's with an issue link of type `Duplicate` (the source
"duplicates" the target; configurable per project as `jira.merge_link_type`); (2) it comments on the source issue: *"Team Tracker:
WEB-105 was merged into WEB-101 (PROJ-7). Its time and evidence now live on WEB-101."* It does **not** transition or close the source
issue (that is a workflow decision for the Jira owners) and it never deletes anything. If the target has no issue (not opted in) only
the comment is posted, naming the Team Tracker key. If the link type does not exist in that Jira the comment is still posted and the
sync log row records `link_error`. Idempotent: a successful `merge` row in `jira_sync_log` for the source marks it announced, so a
job retry or a replayed event posts nothing twice; a Jira outage (`JiraUnavailable`) is retried by the task's backoff with nothing
logged, a permanent `JiraError` is logged as a failed `merge` row. Projects without the projection, sources without an issue and
instances without a connection are skipped. Tested with the fake Jira (`tests/api/test_jira_merge.py`).
