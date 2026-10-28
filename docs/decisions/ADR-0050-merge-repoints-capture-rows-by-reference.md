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
