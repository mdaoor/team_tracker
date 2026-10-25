---
# Machine-readable list. `prefix.*` matches any event type that starts with `prefix.`.
# Adding an event type means adding it here (with its payload schema below); a test fails on unknown types.
event_types:
  # tenancy
  - workspace.created
  - member.added
  - member.updated
  - member.removed
  - person.erased  # GDPR erasure: counts of comments / daily logs / tokens / raw inputs scrubbed
  - team.created
  - team.updated
  - membership.changed
  - reporting_line.changed
  - context_note.*
  # projects
  - project.created
  - project.updated
  - project.priority_changed
  - project.visibility_changed
  - project.process_changed
  - project_member.added
  - project_member.removed
  - iteration.*
  - milestone.*
  - release.*
  - component.*
  - recurrence.*  # created, updated, deleted, materialised
  # work items
  - item.created
  - item.matched
  - item.updated
  - item.state_changed
  - item.reopened
  - item.finished
  - item.blocked
  - item.priority_changed
  - item.impact_changed
  - item.visibility_changed
  - item.project_changed
  - item.team_changed
  - item.parent_changed
  - item.planned_end_set
  - item.field_conflict
  - item.deleted
  - item.linked
  - item.unlinked
  - item.custom_field_set
  - item.progress_recomputed  # nightly heal of rolled-up progress (before/after)
  - custom_field.*  # created, updated, deleted
  # capture
  - activity.started
  - activity.paused
  - activity.resumed
  - activity.ended
  - activity.blocked
  - time.logged  # payload.after may carry calendar_ref{provider,event_id,series_id,instance_start} (Phase 15, ADR-0042)
  - time.unlogged  # Phase 15: a person removed a time entry they sent ("Unsend"); payload before{time_entry_id,minutes,date}, corrects=client_id
  - time.unlog_ignored  # Phase 15: an unlog request whose target was not found or not the actor's (warning unlog_target_not_found)
  - evidence.added
  - estimate.set
  - comment.*
  - attachment.*
  - tag.created
  - tag.linked
  - tag.unlinked
  - tag.merged
  - checklist.*
  - baseline.captured
  - dailylog.drafted
  - dailylog.confirmed
  - dailylog.reviewed
  - dailylog.summarised
  # overlay
  - dimension.*
  - dimension_value.*
  - classification.changed
  - classification.confirmed
  - process.*
  - stage.*
  - stage.moved
  - approval.requested
  - approval.decided
  - approval.resolved
  - product.*
  - product_link.*
  - objective.*
  - key_result.updated
  - weight.recomputed
  - weight.overridden
  - risk.*
  - risk.linked
  - risk.unlinked
  - risk_action.*
  - review.created
  - review.resolved
  - review.updated
  - glossary.learned
  - translator_rule.*
  # governance
  - policy.*
  - violation.opened
  - violation.acknowledged
  - violation.resolved
  - violation.waived
  - violation.false_positive
  - violation.auto_resolved
  - habit.updated
  - habit.flagged
  - habit.status_changed
  - metrics.snapshot_written
  - notification.*  # sent, read
  - insight.generated
  - insight.status_changed
  # views, access, integrations
  - view.*
  - presentation.*  # created, updated, deleted, slide_added, rendered, shared (entity_type `saved_view`, ADR-0021)
  - grant.*
  - role_assignment.*
  - jira.pushed
  - jira.push_failed
  - jira.imported
  - item.jira_linked
  - plan.created
  - product.risk_linked
  - integration.jira_configured
  - project.jira_configured
  # auth
  - token.created
  - auth.login
  - agent.suggestion_written
  - auth.access_requested
  - idp.groups_applied
  - idp.synced
  - idp_mapping.*
---

# Event catalogue

`Event.event_type` is dot-namespaced `entity.verb`. Update payloads always include
`{before?, after?, fields_changed[]}`. Each phase adds its own types to the front matter above
and documents the payload schema in a section below.

## Payload schemas

_Phase 0 defines no concrete emitters; payload schemas are added by the phase that introduces
each event type (Phase 1 starts with `item.created`, `activity.*`, `time.logged`, `evidence.added`)._

### Phase 15 (personal tracker send path)

| event | entity | payload |
|---|---|---|
| `time.logged` | work_item | `after{time_entry_id, minutes, date, calendar_ref?}`; the primary event of the message also carries `result` as before |
| `time.unlogged` | work_item | `before{time_entry_id, minutes, date}`, `corrects` (the original `client_id`); primary event of the unlog message, so a retry deduplicates by its own `client_id` |
| `time.unlog_ignored` | person | `after{corrects}`; written so the unlog message stays idempotent when the target is unknown or belongs to someone else |

A second send of the same calendar occurrence (same workspace, person, `calendar_ref.provider`, `event_id`, `instance_start`) writes **no** event and answers
`status: deduplicated` (ADR-0042). Titles, attendees and notes of a calendar entry never reach the server except in the `note` the person chose to send.
