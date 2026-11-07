# Appendix A — API conventions

## URL shape
- Base: `/api/v1/w/{workspace_slug}/…` for everything workspace‑scoped; `/api/v1/me/…` for the caller; `/auth/…`; `/healthz`, `/readyz`; `/presentation`.
- Resources are plural nouns; sub‑resources nest one level max (`/items/{key}/placements`); actions use `:verb` suffix (`/items:bulk`, `/policies/{id}:test`).
- Items are addressed by **key** (`WEB-142`) in URLs; ids are accepted too (`/items/{id_or_key}`); responses always include both.
- Lists: cursor pagination `?cursor=&limit=` (default 50, max 200) returning `{data: [], next_cursor}`; filterable lists accept `?filter=<base64url JSON of the grammar>` or `POST …/query` with a body.
- Field selection `?fields=a,b,c`; embedding `?embed=activities,risks`.

## Request/response
- JSON only; `Decimal` serialised as string; timestamps RFC 3339 UTC; dates `YYYY-MM-DD`.
- Enum values exactly as in `tt_schema.enums`.
- Every response carries `X-Request-Id`; errors: `{"error": {"code": "snake_case", "message": "...", "pointer": "/messages/2/item", "details": {}}}` with HTTP 400 (validation), 401, 403, 404 (also for invisible objects), 409 (conflict / `If-Match`), 422 (schema violation of a `ClientBatch`), 207 (partial batch), 429.
- Optimistic concurrency on overlay writes: `ETag`/`If-Match` using `updated_at`+`version`.
- Idempotency: capture via `client_id`; other POSTs accept `Idempotency-Key` header (stored 24 h).

## Auth
- `Authorization: Bearer tt_<token>` or session cookie; token scopes `capture|read|manage|admin`.
- Dev only: `X-Debug-Actor: @handle` when `TT_DEV_AUTH=1`.

## Versioning
- Path `v1`; additive changes only within v1; breaking → `v2`. `context_schema_version` for CEL; `ClientBatch.client_version` for clients; `Snapshot.schema_version` (add, start at 1).

## Event catalogue (initial)
`event_type` is dot‑namespaced `entity.verb`. Payload always includes `{before?, after?, fields_changed[]}` for updates.

```
workspace.created member.added member.updated member.removed team.created team.updated membership.changed reporting_line.changed context_note.*
project.created project.updated project.priority_changed project.visibility_changed project.process_changed project_member.added project_member.removed
iteration.* milestone.* release.* component.* recurrence.* (created updated deleted materialised)
item.created item.matched item.updated item.state_changed item.reopened item.finished item.blocked item.priority_changed item.impact_changed
item.visibility_changed item.project_changed item.team_changed item.parent_changed item.planned_end_set item.field_conflict item.deleted
activity.started activity.paused activity.resumed activity.ended activity.blocked
time.logged evidence.added estimate.set comment.* attachment.* tag.created tag.linked tag.unlinked tag.merged checklist.* baseline.captured
dailylog.drafted dailylog.confirmed dailylog.reviewed
dimension.* dimension_value.* classification.changed classification.confirmed
process.* stage.* stage.moved approval.requested approval.decided approval.resolved
product.* product_link.* objective.* key_result.updated weight.recomputed weight.overridden
risk.* risk.linked risk.unlinked risk_action.*
review.created review.resolved glossary.learned translator_rule.*
policy.* violation.opened violation.acknowledged violation.resolved violation.waived violation.false_positive violation.auto_resolved
habit.updated habit.flagged habit.status_changed metrics.snapshot_written notification.sent insight.generated insight.status_changed
view.* grant.* role_assignment.*
jira.pushed jira.push_failed jira.imported
```
Every phase adds its types to `docs/api/event-catalogue.md` with payload schema; a test fails on unknown types.
