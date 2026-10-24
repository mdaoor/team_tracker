# Governance screens (Phase 9)

Routes live under `/w/:ws/…`. Controls render from `GET /me` → `can{}`; the server enforces every permission regardless.

| screen | route | permission | what it does |
|---|---|---|---|
| Policies | `policies`, `policies/new`, `policies/:key` | `policy.read` list; `policy.write` create/dry-run/activate; `policy.admin` raw CEL, "Activate anyway" | list with last dry run and open violations; template gallery → parameter form generated from the template's `params`; CEL editor (CodeMirror 6, autocomplete from `GET /governance/context-schema`, 400 ms debounce → `POST /policies:check`, errors with field and position); "Describe the rule" → `POST /policies:author` (a draft; `validated` is set by this server, never by the model); dry run → fired count and sample; activation needs a run after the last edit |
| Violations | `violations` | read: ACL-filtered; `violation.resolve`, `violation.waive` | grouped severity → policy; evidence as chips; row and bulk actions; waive / false positive need a reason; keys `a r w f` |
| Habits | `habits` | ACL-filtered; `habit.coach` | per person, positive first, trend glyphs, drawer with linked violations, status and coaching note; wellbeing collapsed and marked restricted when none are visible; "Export for 1:1" (stamped CSV, no notes) |
| Performance | `performance` | metric snapshots follow ACL | line charts for the snapshot metrics, previous-period overlay, table toggle, stamped CSV, scheme selector |
| Digest | `digest` | `dailylog.review` | daily logs (mark reviewed), notable events, pinned notes, insights for a team and window |
| Insights | `insights` | project visibility | status actions, regenerate per project, evidence links open the item drawer |
| Weights | `weights` | `weight.set` | formula builder, preview on 20 sample items (server `evaluate`), save + recompute, per-item override |
| Notifications | bell in the header | own | unread count from `/notifications/mine`, mark read on click |

The item drawer has a **violations** tab (`GET /violations?target_id=`).

## Keyboard
`a` acknowledge · `r` resolve · `w` waive · `f` false positive (violation row focused or selected) · `⌘K` command palette · `Esc` closes a drawer.

## Not built in this phase
Version history of a policy, a histogram in the dry-run panel, a scope picker and notify/auto-action editors beyond the common cases
(notify the manager; raise priority to high), Storybook stories, the accessibility audit score, and a Slack channel for notifications (Phase 11).
