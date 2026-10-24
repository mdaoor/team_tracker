# Phase 5 — Web shell and manager console

## Goal
The manager's web app: login, workspace switcher, the one‑time **Setup** editors (projects & products, process & stages, dimensions, people & teams, milestones & iterations, risks, tags & glossary, translator rules, manual‑mode required fields), the daily **Review queue**, and the shared app shell (top bar, scope selector, dimension filter chips, routing) that the P6 views plug into. Policies and Weights screens come in P9; P5 leaves their nav entries as "coming soon".

## Depends on
Phase 4 (auth, scopes, permissions), Phase 3 (all overlay APIs), Phase 2 (glossary/rules APIs).

## Inputs
- UX §3 (all), §4.1 (global controls), §6, §7.2
- Design tokens from `docs/spec/team_tracker_presentation.html` `:root`

## Scope
1. App shell (`apps/web`): TanStack Router routes, auth guard, workspace switcher, persistent top bar (scope ▾, dimension filter chips, date range, layout tabs placeholder, weight scheme ▾, save view — the last three are wired in P6), command palette (`⌘K`) for navigation, toast/undo system, provenance hover chip component, optimistic concurrency handling (`If-Match`, 409 → diff dialog with one‑click revert, UX §3.4).
2. Setup editors (UX §3.1/3.3):
   - **Projects & products** tree editor (product → project → epic), drag‑to‑reparent, right pane form, "add project asks only name/key/process".
   - **Process editor**: horizontal stage chips, drag reorder, chip popover (expected duration, WIP limit, approval toggle, `maps_to_state_category`, entry/exit criteria), live mini‑pipeline of current distribution.
   - **Dimensions**: add dimension (name, tree/flat, single/multi, applies‑to), inline values, CSV paste.
   - **People & teams**: org/virtual teams tree, membership with allocation %, reporting lines editor (kind, scope, validity), capacity; **Identity provider** panel (P4 §4.6): LDAPS/OIDC settings, test connection, AD group → role/team mapping table with group picker (searches AD), "Sync now", last sync log, Kerberos status.
   - **Milestones & iterations** calendar editor (month view, drag to move dates, iteration bars).
   - **Risks** register table + 5×5 heat‑map; drag risk card onto tree node → `RiskLink`.
   - **Tags & glossary**: tag merge, provisional tag review, glossary phrase → target typeahead, learned entries with hit counts.
   - **Rules for the translator**: free‑text list, reorder, dry‑run panel "would have changed N of last 200 lines" (needs endpoint `POST /translator-rules:dry-run` — build it here using the `tt_client` structured parser plus the LLM gateway's `/v1/rules/dry-run`, Phase 2b).
   - **Manual mode & context pack**: required fields per workspace/project, custom field definitions, "Export context pack" (download JSON for air‑gapped Mode B), Jira link per project (fields only; sync in P10).
3. **Review queue** screen exactly as UX §3.2: flat list, one‑click default action = suggestion, alternatives, bulk bar, keyboard (`j/k`, `Enter`, `x`), scoped automatically, empty state "You're all caught up".
4. Item detail drawer (shared with P6): capture fields read‑only, overlay fields editable inline with provenance chips, activity timeline, evidence list, placements history, risks, violations (P8 fills), comments.
5. Accessibility & i18n foundations: keyboard navigable, ARIA on all custom controls, `en` strings in a catalogue; RTL‑ready layout (the team is bilingual).
6. E2E tests with Playwright against the seeded dev stack.

## Out of scope
Views (P6), presentation page (P7), policy editor (P9), Jira sync UI beyond fields (P10).

## Detailed tasks

### 5.1 Shell & infrastructure
- Generated API client (`openapi-typescript` + `openapi-fetch`), TanStack Query with per‑workspace cache keys, error boundary → toast with request id.
- Auth: redirect to `/auth/login`; `me` query; workspace switcher from `/me/workspaces`; scope selector from `/me/scopes` (P4).
- Layout: left nav (Setup / Review queue / Views / Policies / Weights), top bar per UX §4.1, content, right drawer slot.
- Design system: tokens from the prototype (`--surface, --surface-2, --line, --text*, --s1..s8, --good/--warn/--serious/--crit, --seq1..6`), components: Button, Select, Chip, Pill (severity variants from prototype), Card, Table (TanStack Table, virtualised), Drawer, Popover, Tree, Sparkline (SVG), HeatMapCell, ProvenanceChip, ConfidenceCue (`✓ / 92 % / suggested / ask`).
- Concurrency: every mutation sends `If-Match`; on 409 show "Changed by @x 2 min ago" with their value, yours, `Keep mine` / `Take theirs` / `Revert`.
- Undo: mutations that are reversible (classification, stage move, priority) register an undo action for 10 s.

### 5.2 Setup editors — behaviours worth specifying
- Projects & products tree: lazy‑load children; drag between products updates `ProductLink{delivers}` (confirm dialog shows propagation preview from `items:bulk-preview`); keyboard reparent via `⌥←/→`.
- Process editor: reordering calls `PATCH /processes/{id}/stages:reorder`; changing `maps_to_state_category` warns if items currently in that stage would change state (count from board endpoint) and requires confirmation; mini‑pipeline uses `/processes/{id}/board` counts, colour from `--seq`.
- Dimensions: creating a dimension with `applies_to` excluding `work_item` hides it from item filters; tree values support indent/outdent with Tab/Shift‑Tab.
- Reporting lines: validation messages mirror server errors (one primary line manager; scope required for `project|team|product` kinds).
- Risks heat‑map: cells = count of risks at (p,i), click → filtered table; drag card uses HTML5 DnD with a drop target overlay on the tree.
- Glossary: typeahead searches projects/items/people; "learned" rows show `hits` and `Promote to manual` / `Delete`.
- Translator rules dry‑run: panel lists sample lines whose parse would change, before → after per field.
- Manual mode: checklist of fields with Required toggle, per project override; "Export context pack for @person" (admin only) downloads what that person's `GET /context-pack` returns.

### 5.3 Review queue
- Data: `GET /review-queue?status=open&limit=50` + live updates via SSE `GET /review-queue/stream` (add endpoint: server‑sent events on `review.created|resolved` for the actor; fallback polling 30 s).
- The default (suggested) button comes from `ReviewItem.suggestion/confidence`, filled by the P3 pipeline and refined by the LLM gateway's `/v1/suggest/review` job (Phase 2b).
- Row renderers per kind (UX §3.2 mock rows): unclassified → `[Suggested ✓] [other…]`; stage suggestion → `[test ✓] [keep build]`; provisional tag → `[merge into …] [keep]`; missing estimate → `[set …h] [waive]`; risk mention → `[link R7] [new risk] [ignore]`; unknown person → `[pick person] [ignore]`; low‑confidence match → `[confirm match] [split into new item]`.
- Bulk: `Bulk: assign project ▾`, `Confirm all suggested` (only rows with confidence ≥ 0.7 by default; slider).
- After resolve: row collapses with a 3‑s undo; the learned glossary toast "Next time 'the ingestion retry' → DATA" with `Don't learn`.

### 5.4 Item drawer
- Header: key, title (capture, read‑only, with "reported by @x via assisted|manual on <date>"), priority dot, weight, risk chip (highest open score), stage age colouring (amber at expected, red at 2×).
- Tabs: Overview (overlay fields inline‑editable, provenance on hover), Activity (activities + time entries timeline), Evidence, Placements (history table), Links & risks, Comments, Events (audit, redacted per ACL).
- Inline edit controls: priority select, impact 1–5, weight per scheme (shows scheme selector), stage (pipeline chips), classifications per dimension (typeahead), assignees with roles, milestone/iteration/release/component selects, planned dates, estimate (writes `Estimate{revised}`), tags.

### 5.5 Tests
- Vitest: components (confidence cue, pill severity mapping, conflict dialog), hooks (If‑Match retry), route guards.
- Playwright (seeded stack, user `@mahmoud` as manager): (a) create a project with name/key/process only; (b) reorder stages and see mini‑pipeline update; (c) resolve a review‑queue row and verify the glossary entry appears; (d) drag a risk onto an epic and see children get the chip; (e) conflict dialog when two tabs edit the same priority; (f) keyboard‑only pass through the review queue.

## Data & API contracts (additions)
- `GET /review-queue/stream` (SSE), `POST /translator-rules:dry-run`, `PATCH /processes/{id}/stages:reorder`, `GET /me/workspaces`, `GET /context-pack?for=@handle` (admin), `GET /items/{key}/events` (redacted audit).

## Acceptance criteria
- [ ] UX §7.2 walk‑through completes in the UI in ≤ 6 clicks: confirm a match, drag a risk onto an epic (children inherit), set weight inline.
- [ ] Adding a project requires only name, key, process; defaults are visible afterwards.
- [ ] Review queue is scoped: two managers logged in see disjoint queues for the same seed.
- [ ] Every overlay value shows a provenance chip with `set by · when · inherited from` matching `/provenance`.
- [ ] 409 conflict flow works and the revert restores the prior value (verified via events).
- [ ] Lighthouse accessibility ≥ 95 on Setup and Review queue; all interactive elements reachable by keyboard.
- [ ] No API call bypasses the generated client (lint rule).
- [ ] Playwright suite green in CI (headless, seeded container).

## Exit checklist
- [ ] Storybook (or Ladle) stories for all shared components.
- [ ] `docs/web/README.md`: architecture, state management rules, how to add a screen.
- [ ] ADR‑0009 "SSE for review queue updates".

## Handover notes
P6 adds the Views area and reuses the shell, top bar, drawer and filter chips; keep the filter chip state in the URL (`?f=` base64 JSON of the `SavedView.filter`) from day one so views are shareable URLs (UX §4).

> **Update (Phase 2b built):** review-queue suggestions arrive stored on the row (`ReviewItem.suggestion.llm`, `GET/PATCH /review-items/{id}`); translator-rules dry-run is `POST /v1/rules/dry-run` on the gateway.
