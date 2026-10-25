# UX addendum — role modes, the command card and the effect preview (Phase 14)

Addendum to `team_tracker_ux.md` §2 (the capture palette). Nothing in §2 changes: capture rows still have no Confirm step and are never
rejected. This adds the management side of the same client.

## 2.7 Role switch

The palette header gains a role switch. It lists only the modes the server allows this person (`GET /me` → `role_modes`); a person with
no role beyond reporter sees no switch and no management UI at all. The mode is a lens: it changes the verbs offered first, not what the
server permits.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Role mode [ Manager ▾ ]      · 2 in your queue                             │
│  [New epic] [Merge] [Tag] [New team] [Move stage] [Approve] [Waive]         │
│                                                                             │
│  Tell me what to do ▸ merge WEB-101 and WEB-105▌                            │
│  Or fill a form [ Choose an op… ▾ ] [Open form]   [My queue] [Team now]     │
└─────────────────────────────────────────────────────────────────────────────┘
```

Role modes and what they surface first: reporter (confirm my day, notes), manager (items, teams, stage moves, approvals, review, waive),
scrum master (iterations, move to iteration, stage moves, WIP and review), coach (habits and coaching notes; no item writes), pm
(projects, plans, milestones, risks), admin (teams, reporting lines, tags, policies). Chips prefill the input; they never send.

## 2.8 Command card

A command always shows what it will do and needs one confirmation (capture does not). Arguments first, then the **Effect preview**
computed by the server without writing, then Confirm.

```
┌ item.merge · destructive ───────────────────────────────────────────────────┐
│  sources      WEB-101, WEB-105                                              │
│  target       WEB-101                                                       │
│                                                                             │
│  Effect preview                                                             │
│    Removes           WEB-105                                                │
│    Affects           2 items                                                │
│    Children          2 child items                                          │
│    Overrides kept    1                                                      │
│                                                                             │
│  [ Confirm ]  [ Discard ]                         role: Manager             │
└─────────────────────────────────────────────────────────────────────────────┘
```

Ordinary command: one Confirm (CLI: `--yes` skips it). Destructive command (merge, split, remove members, reporting line, policy
deactivate, bulk moves over 5 items): Confirm only arms it, then a second question appears with focus on "Yes, do it"; `--yes` alone
never skips it, `--force` does and is recorded. A command the server would reject shows the reason in place of the preview and has no
Confirm button.

```
┌ team.create ────────────────────────────────────────────────────────────────┐
│  name payments · kind virtual · lead @mahmoud · members @sara, @ali         │
│  Effect preview                                                             │
│    Creates      team payments, 2 members                                    │
│  [ Confirm ]  [ Discard ]                                                   │
└─────────────────────────────────────────────────────────────────────────────┘
┌ project.create ─────────────────────────────────────────────────────────────┐
│  Effect preview                                                             │
│    Rejected: role manager may not create projects (needs project.create)    │
│  Nothing can be sent: the server would reject this.      [ Discard ]        │
└─────────────────────────────────────────────────────────────────────────────┘
```

Offline the preview section reads "Offline: the server will validate this when it is sent"; Confirm queues the file.

## 2.9 Forms from the catalogue

"Or fill a form" renders any op from its JSON Schema: required fields marked "(required)" and enforced, enums as selects, lists as comma
separated inputs with pack-backed suggestions (people, projects, teams, items), booleans as checkboxes, dates as date inputs. The same
fields spec drives the CLI prompts (`tt cmd <op> --form`) and the Slack modal. Server-side validation errors are shown next to the field
(`aria-invalid`, described-by).

## 2.10 My queue and Team now

```
My queue                                         Team now
  RV-3 · Tag auth?        [Accept accept]          @sara   WEB-1  Login redirect loop
  RV-4 · Merge dupes?     [Accept accept]          @ali    DATA-2 Retry refactor
  RV-5 · (no default: use the web console)
  [Accept all 2 defaults]
```

## 2.11 Slack

Same card as blocks (Confirm / Edit / Discard; a destructive Confirm opens Slack's confirmation dialog). Approvals arrive as messages with
**Approve** and **Reject** buttons; a violation has **Waive…**, which asks for the reason in a modal.

## Accessibility

All controls are native buttons, inputs and selects in reading order; the preview is a labelled region; errors use `role="alert"` and
`aria-describedby`; the destructive second question moves focus to its confirm button. The desktop tests run axe (WCAG 2 A/AA and best
practice) over the header, card, destructive confirmation, form and both panels.
