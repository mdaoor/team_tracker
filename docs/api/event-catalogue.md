---
# Machine-readable list. `prefix.*` matches any event type that starts with `prefix.`.
# Adding an event type means adding it here (with its payload schema below); a test fails on unknown types.
event_types:
  # tenancy
  - workspace.created
  - member.added
  - member.updated
  - member.removed
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
  - recurrence.materialised
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
  # capture
  - activity.started
  - activity.paused
  - activity.resumed
  - activity.ended
  - activity.blocked
  - time.logged
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
  - notification.sent
  - insight.generated
  - insight.status_changed
  # views, access, integrations
  - view.*
  - grant.*
  - role_assignment.*
  - jira.pushed
  - jira.push_failed
  - jira.imported
  # auth
  - token.created
---

# Event catalogue

`Event.event_type` is dot-namespaced `entity.verb`. Update payloads always include
`{before?, after?, fields_changed[]}`. Each phase adds its own types to the front matter above
and documents the payload schema in a section below.

## Payload schemas

_Phase 0 defines no concrete emitters; payload schemas are added by the phase that introduces
each event type (Phase 1 starts with `item.created`, `activity.*`, `time.logged`, `evidence.added`)._
