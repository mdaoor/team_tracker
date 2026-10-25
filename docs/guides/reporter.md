# Reporter guide

Tell Team Tracker what you did in one line; your manager's structure is applied for you.

**CLI:** `tt say "fixed the login loop WEB-142, took 2h #auth plan tomorrow 5pm"` (shorthands: `tt doing/done/pause/resume/blocked/note/time/plan/est/new`).
Tokens: `@person` mentions, `#tag`, `p:web` project, `!high` priority, `est 3d`, `KEY-123` references, several tasks at once with `1. ... 2. ...`.
A card shows what was understood with a cue per row (`✓ matched`, a percentage, `suggested`, `ask`). `Enter` sends; the JSON is kept in your outbox (`tt outbox`, `tt edit`, `tt send`).
**Manual mode:** `tt form` - choices come from your context pack; fields your workspace requires cannot be skipped. Offline? Everything queues and `tt sync` delivers it exactly once.
**Slack / desktop palette:** same flow with buttons (`docs/client/slack.md`, `docs/client/desktop.md`). **Today:** `tt today` shows your day; confirm it to send it to your manager.
Anything the system could not place goes to your manager's review queue; you never need to guess a project. Your Inbox is private to you.
