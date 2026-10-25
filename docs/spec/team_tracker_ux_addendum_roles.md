# UX addendum: role modes, the command card and the effect preview (Phase 14)

## Role switch
The palette header shows the current mode and offers only the modes the person has (`GET /me → role_modes`):

```
┌ Team Tracker ─────────────────────────────  [ Manager ▾ ] ┐
│  ›  merge WEB-101 and WEB-105 into one                     │
└─────────────────────────────────────────────────────────────┘
        modes: Reporter · Manager · Scrum master · Coach · PM · Admin
```
The mode changes the verb chips and the forms offered first (scrum master: iteration, stage, WIP, review; coach: habits and notes;
PM: plan, milestone, baseline; manager: team, assign, approve; admin: policies, people). Everything the person may do remains
reachable by typing. A reporter without roles sees no management verbs at all.

## Command card with effect preview
Typing (assisted) or filling a form (manual) produces the same card. The preview comes from the server and never writes:

```
┌ Merge items ──────────────────────────────────────────────┐
│ WEB-101 ← WEB-105                          destructive     │
│ moves 3 activities · 150 min · 2 evidence · 1 link         │
│ deletes WEB-105 (merged into WEB-101)                       │
│ overrides kept: 1 manual classification                     │
│                                  [ Cancel ]  [ Confirm ]    │
└─────────────────────────────────────────────────────────────┘
```
Ordinary ops need one confirmation (or `--yes`); destructive or wide ops need the confirmation of the preview (`--yes` alone does not
bypass; `--force` does and is logged). A rejected command shows the reason in place ("missing permission project.create") and
keeps the card editable.

## Panels
* **My queue**: review rows with the one-click defaults of Phase 13 R6.1; resolve inline.
* **Team now**: active work of the people I manage or lead (`GET /activities?team=mine`).
* Slack: `/tt do …`, modal forms generated from the catalogue, approval and waive as message buttons.
