# Phase 15 — Personal time tracker with Google Calendar, Outlook and Microsoft Teams integration

## Goal
The client app becomes a **personal time tracker** that is useful even if the person never sends anything to the Team Tracker server: plan and track my day, week and month; recurring personal tasks (training, courses, reading, sport, focus blocks); a running timer; meetings pulled automatically from Google Calendar and Outlook/Microsoft 365 (including Teams meetings) and turned into **ready‑to‑send tasks**. Everything is **local and private by default**; sending to the server is optional, per item or per category, and takes one tap. Work‑related items (meetings on project topics, development/training the person chooses to share) flow into the existing capture pipeline as ordinary `ClientMessage`s, so the server needs very little new. The guiding aim is the UX law of the spec: the reporter's effort is one line or one tap.

## Depends on
Phase 2 (client core, outbox, pack), Phase 11 (desktop/mobile shells), Phase 13 R5.3 (responsive), Phase 14 (role modes share the same shell). Server changes are small and listed below.

## Inputs
- Product requirement (this planning round): *personal time tracker app that could optionally send or report work tasks to the server; integrate with Google Calendar, Outlook Calendar and Microsoft Teams to pull meetings as automated tasks ready to send; manage my day, week, month; recurring tasks like training, courses, reading, sports, by default not sent to the server; training can be sent as personal development; the integration should make life easier for anyone who sends tasks; UX very simple to send.*
- UX §2 (palette, verbs, micro‑interactions, end of day), §0 reporter metrics
- Object model §7 capture tables and §12 client contract (what a sent item becomes)

## Design principles
1. **Local‑first, private by default.** Personal data lives in an encrypted local store on the device. Nothing leaves the device except what the person explicitly sends (or has set a standing rule to send). Calendar tokens stay in the OS keychain. The server never sees calendar contents.
2. **Two kinds of items, one timeline.** `PersonalItem`s (local) and server `WorkItem`s the person has sent or is assigned (from the ContextPack) appear on the same day/week/month timeline, visually distinct. A personal item can be **promoted** to a server item (sent) and keeps a link; a server item can be **scheduled** locally (time block) without the server knowing.
3. **Meetings become tasks, not noise.** A calendar event becomes a personal item of kind `meeting` with a suggested project (by attendees, title keywords, glossary) and a suggested action: after it ends, one tap sends "meeting · 1 h · Sprint planning" as a `log_time` + `note` to the right project; declined/cancelled/all‑day/private events are ignored by default.
4. **Categories carry the sharing default.** `work` → ask once then remember; `meeting` → suggest after each; `development` (training, courses, reading) → off by default, one toggle sends it tagged `#development` to the person's development project; `personal` (sport, errands, health) → never offered.
5. **Recurring is first‑class.** iCal RRULE on personal items; instances materialised locally; skipping/completing an instance never touches the server; a recurring development item can be set to "send each completed instance".
6. **Same send path.** Sending = creating a `ClientBatch` in the outbox (Phase 2 §2.6) with `source.origin=personal|calendar:<provider>` and `calendar_ref{provider,event_id,series_id}`; the server deduplicates by `client_id` and (new) by `calendar_ref` so a meeting is never logged twice.

## Scope

### 15.1 Local store and model (`tt_client/personal/`)
- SQLite (SQLCipher or OS‑level encryption) at `~/.local/share/tt/personal.db`; schema versioned with its own migrations.
- `PersonalItem{id, title, kind: task|meeting|focus|training|course|reading|sport|habit|errand|other, category: work|meeting|development|personal, start?, end?, all_day, duration_min?, rrule?, series_id?, instance_date?, status: planned|active|done|skipped|cancelled, notes, tags[], project_hint?, item_hint? (server key), share_policy: never|ask|auto, sent_refs[{client_id, server_key, at}], calendar_ref{provider, calendar_id, event_id, ical_uid, etag}?, attendees[]?, location?, online_meeting_url?, created_at, updated_at, deleted_at}`.
- `Timer{item_id, started_at, paused_at, segments[]}` (one active timer; starting another pauses the current — same rule as the server).
- `Rule{match: {kind?, title_regex?, attendee_domain?, calendar_id?, keyword?}, set: {category, project_hint, share_policy, tags}}` — user rules and learned rules (from accepted suggestions).
- Export/import of the personal store (JSON) for backup and device move; "Erase all local data".

### 15.2 Day / week / month UX
- **Today** (default screen): timeline with meetings (from calendars), planned items, timer; a "ready to send" tray at the bottom listing items with pending suggestions ("Send 1 h Sprint planning → WEB?" [Send] [Edit] [Not work]); one‑line quick add at the top (same palette, parses times "9–10 reading", "run 30m at 6pm", "course: Rust ch.3 1h").
- **Week**: 7‑column board with drag to reschedule, capacity bar per day (meetings + planned vs available hours from `WorkspaceMember.capacity_hours_per_week` or a local setting), recurring instances shown with a ↻ glyph.
- **Month**: calendar grid with dots per category and totals (hours work / development / personal) and a sparkline of sent vs private time.
- **Recurring editor**: natural text ("every weekday 7:00 run 30m", "Tue/Thu 18:00 Arabic course 1h", "read 20m daily") → RRULE preview; end conditions; per‑series share policy.
- **Review end of day**: one card: hours by category, items done, the unsent work items with [Send all] [Pick] [Skip]; confirms the server `DailyLog` only for what was sent.
- **Weekly review**: planned vs done, development hours, meetings load, streaks for habits; optional "share development summary" to the server as a `note` on the development project.
- **Simplicity rules**: every send is one tap from where the item is shown; no modal unless a project is unknown (then a 3‑choice chip row from the pack); all text entry is one line; default shortcuts `N` new, `Space` start/stop timer, `S` send selected, `D/W/M` switch views.

### 15.3 Calendar and Teams integration (`tt_client/integrations/`)
- **Google Calendar**: OAuth 2.0 PKCE (desktop loopback / mobile app scheme), scopes `calendar.readonly` (+ `calendar.events` only if the "write focus blocks back" option is enabled); `calendarList` to pick calendars; incremental sync with `syncToken`; push notifications not required (poll every 5 min while running, on wake, and on demand).
- **Microsoft 365 / Outlook / Teams**: Microsoft Graph with MSAL (PKCE, work or school and personal accounts), scopes `Calendars.Read` (+ `Calendars.ReadWrite` optional), `OnlineMeetings.Read` optional; `/me/calendars`, `/me/calendarView` with delta queries; Teams meetings are calendar events with `isOnlineMeeting`/`onlineMeeting.joinUrl` — we use that; attendance reports (`/me/onlineMeetings/{id}/attendanceReports`, needs admin consent) are optional to compute actual attended minutes.
- **Normalisation**: both providers → `CalendarEvent{provider, calendar_id, event_id, ical_uid, series_id, title, start, end, all_day, status, response (accepted|tentative|declined|organizer), attendees[], organizer, location, online_url, is_private, categories[]}`; stored locally as `PersonalItem{kind=meeting}` linked by `calendar_ref`.
- **Rules & suggestions**: skip declined/cancelled/all‑day/private/"Out of office"/focus‑time events by default (configurable); map to project by: attendee email domain ↔ client/product, attendee set ↔ team ↔ team's projects, title keywords ↔ glossary (`"sprint planning" → iteration ceremony of the team's active project`), recurring series memory (once mapped, stays mapped); confidence shown as the usual cue.
- **After the meeting**: at `end + 2 min` (if the app is running) or at next launch, the item appears in the ready‑to‑send tray with duration = scheduled (or attended minutes when available) and a one‑line note template ("Sprint planning with @sara, @ali"); [Send] creates `log_time` (+ `note`) on the mapped project/item; organiser's meetings can also create a `task` ("follow‑ups: …") in manual mode.
- **Write‑back (optional, off by default)**: create calendar "focus" blocks for planned personal/work items so the person's colleagues see them busy; never writes task titles for `personal` category (writes "Focus").
- **Teams presence/chat**: out of scope for v1 (document); only meetings.
- **Token storage**: OS keychain (macOS Keychain, Windows Credential Manager, libsecret) via the Tauri store plugin; CLI uses the same keyring; tokens never in the outbox or logs.
- **Offline**: last sync cached; items editable; sends queue as today.

### 15.4 Server changes (small)
- `ClientMessage.source` gains `origin: manual|assisted|personal|calendar:google|calendar:microsoft` and `calendar_ref{provider,event_id,series_id,instance_start}`; dedup index on `(workspace_id, person_id, calendar_ref.provider, calendar_ref.event_id, instance_start)` for `time.logged` events (second send of the same meeting instance → `deduplicated`).
- **Development project**: workspace setting `development.project_key` (default: a per‑workspace project `DEV` created on first use, kind standard, visibility `private` per member via item visibility `private` + tag `#development`) so development items land somewhere visible to the person and their line manager only; policy templates gain `development_hours_below {hours/week}` (positive habit `learning_consistency`).
- ContextPack adds `development_project`, `calendar_mapping_hints` (team → projects, client domains → products), `my_capacity_hours_per_week`.
- Nothing else: meetings are `log_time`/`note`; training is `log_time` on `DEV`; everything else stays local.

### 15.5 Shells
- Desktop (Tauri): Today/Week/Month windows plus the palette; tray shows timer; notifications for "meeting ended — send?" and end‑of‑day; background sync.
- Mobile (Tauri mobile): Today and Week, quick add, tray; notifications; share‑sheet to add a link as reading.
- CLI: `tt day`, `tt week`, `tt month`, `tt add "<line>"`, `tt timer start|stop`, `tt cal connect google|microsoft`, `tt cal sync`, `tt tray` (ready‑to‑send list with `send <n>`), `tt recur add "<text>"`.
- Slack bot: not part of personal tracking (privacy); only the end‑of‑day DM continues to work for sent items.

### 15.6 Privacy & settings
- First‑run explains the default: "Your personal items stay on this device. Meetings and work you choose to send go to Team Tracker." with the three category toggles.
- Per‑category share policy; per‑calendar include/exclude; "never suggest for these attendees/domains"; local data encryption on; "What has been sent" audit list with server keys; one‑tap "Unsend" within 10 min (sends a correction `update` removing the time entry — server supports `time.unlog` by `client_id`, add it).
- No telemetry of personal items; app analytics (if any) count events, never titles.

## Out of scope
Two‑way sync of tasks to calendars beyond focus blocks; Teams chat/presence; Apple/CalDAV calendars (follow‑up: CalDAV generic adapter covers iCloud/Fastmail); shared team calendars; Outlook desktop add‑in.

## Detailed tasks (ordered)
1. Local store, model, migrations, encryption, export/import; unit tests with Hypothesis for RRULE materialisation across DST and time zones.
2. Quick‑add parser extension (times, durations, categories, RRULE phrases) in the deterministic parser; golden set ≥ 60 lines; gateway `translate` prompt variant for personal lines (never sends anything).
3. Timer with pause/switch semantics; integration with server `start/pause/done` when the item is a sent work item.
4. Today/Week/Month UI (desktop first), ready‑to‑send tray, end‑of‑day and weekly review cards; keyboard map; mobile layouts.
5. Google Calendar adapter (PKCE, calendar list, incremental sync, normalisation); Microsoft Graph adapter (MSAL, delta, Teams meeting fields); provider test doubles with recorded fixtures; sync scheduler; token keyring.
6. Rules engine and suggestion scoring; learned rules from accepted suggestions; recurring series memory.
7. Send path: personal → `ClientBatch` with `origin`/`calendar_ref`; server dedup; `time.unlog`; development project provisioning; ContextPack additions.
8. Privacy settings, first‑run, audit list, erase.
9. CLI commands; Slack unchanged; docs.

## Acceptance criteria
- [ ] With no server configured at all, a person can plan a week with recurring sport and reading items, run the timer, and see month totals; nothing is sent and no network call is made (network‑deny test).
- [ ] Connecting Google or Microsoft (recorded fixtures) pulls next week's meetings; declined/all‑day/private events are hidden; a Teams meeting shows its join link and is tagged as a meeting.
- [ ] After a meeting ends, the tray shows it with a project suggestion; one tap sends `log_time` (+ note) to the suggested project; sending the same instance twice yields `deduplicated`; a recurring meeting series remembers its mapping.
- [ ] A training item set to "send as development" lands on the workspace development project, tagged `#development`, visible to the person and their line manager only (ACL test); a `personal` item can never be sent (no control exists; API rejects `category=personal` origin if forged? — no: the server cannot know; the client simply offers no path; document).
- [ ] End‑of‑day card lets the person send all unsent work items in two taps and confirms the server `DailyLog`; weekly review shows development hours and streaks.
- [ ] Tokens are stored in the OS keyring (test on three desktop OSs), never appear in outbox files or logs; "Erase all local data" removes the store and tokens.
- [ ] Reporter metric: from meeting end to sent in ≤ 2 taps and ≤ 5 s; quick add of a personal item in ≤ 3 s with one line.
- [ ] Mobile: Today and Week usable at 390 px with no horizontal scroll; notifications arrive for meeting‑ended and end‑of‑day.
- [ ] Accessibility: all new screens pass axe with 0 serious; full keyboard operation of Today and the tray.

## Exit checklist
- [ ] `docs/client/personal-tracker.md` (model, categories and defaults, calendar setup for Google and Microsoft incl. admin consent notes, privacy), `docs/client/calendar-integrations.md` (scopes, sync, limits, troubleshooting).
- [ ] ADR‑0029 "Local‑first personal store; server sees only what is sent"; ADR‑0030 "Calendar dedup by calendar_ref"; ADR‑0031 "Development project visibility".
- [ ] UX addendum `docs/spec/team_tracker_ux_addendum_personal.md` with Today/Week/Month mocks in text, tray card, end‑of‑day card.
- [ ] Appendix E rows for the requirement; README requirement table updated.

## Handover notes
Keep provider adapters behind one `CalendarProvider` protocol so CalDAV and others can follow; keep suggestion scoring deterministic and testable (the gateway may rank, never decide).
