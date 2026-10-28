# Calendar integrations: Google, Outlook and Teams

One `CalendarProvider` protocol (`tt_client.integrations`), two adapters, plain `httpx`, OAuth 2.0 code + **PKCE** with a loopback redirect
(`http://127.0.0.1:8765/callback`, port in `[calendar] redirect_port`). No MSAL, no Google SDK.

## Configure (`~/.config/tt/config.toml`, never tokens)
```toml
[calendar]
email = "you@company.com"            # who "me" is (to leave yourself out of attendees)
google_client_id = "....apps.googleusercontent.com"
google_client_secret = "..."         # a Desktop-app client; not confidential
microsoft_client_id = "..."          # an app registration, public client, redirect http://127.0.0.1:8765/callback
microsoft_tenant = "common"          # or your tenant id; work/school and personal accounts
microsoft_attendance = "false"       # true adds OnlineMeetings.Read (attended minutes)
```
Env overrides: `TT_GOOGLE_CLIENT_ID`, `TT_GOOGLE_CLIENT_SECRET`, `TT_MS_CLIENT_ID`, `TT_MS_TENANT`. Then `tt cal connect google|microsoft`.

## Scopes and consent
| provider | scope | needed for | consent |
|---|---|---|---|
| Google | `calendar.readonly` | read events | user |
| Microsoft | `offline_access`, `Calendars.Read` | read events, refresh | user (some tenants require admin approval of the app) |
| Microsoft | `OnlineMeetings.Read` (optional) | attended minutes from Teams attendance reports | usually **admin consent** |
Write-back of focus blocks (`calendar.events`, `Calendars.ReadWrite`) is off and not implemented.

## What is pulled
Google: `calendarList`, then `events` with `singleEvents=true`; the first call reads a window (14 days back, 21 ahead) and stores `nextSyncToken`;
later calls send only `syncToken` (410 -> full window again). Microsoft: `/me/calendars`, then `calendarView/delta` for the window; the `@odata.deltaLink`
is stored and called next time (`@removed` = deleted; **HTTP 410 `syncStateNotFound` -> a full window pull again**, same as Google). A full pull is the
truth for the window: unsent meetings of that calendar inside it that the provider no longer lists are dropped, the rest are matched by
`calendar_ref` so nothing is duplicated and local choices survive. A meeting **you deleted** is never brought back by any sync (ADR-0054 section 5). Teams meetings are events with `isOnlineMeeting` and `onlineMeeting.joinUrl`; the join link is
shown. Each event becomes `PersonalItem{kind=meeting}` linked by `calendar_ref`; local choices (project, category, done/skipped, notes) survive re-sync.

**Hidden by default** (each a setting `calendar.skip_*`): declined, cancelled, all-day, private/confidential, out-of-office, focus-time, working-location.
Changing a filter forces one full pull. Per-calendar include/exclude and the never-offer lists have commands and a screen: `tt cal list|exclude|include|suppress|refresh`
and the desktop **Calendars** dialog (see `personal-tracker.md`). The calendar list itself is re-read every `calendar.refresh_hours` (default 24) and on `tt cal refresh`.

## When it syncs
Every 5 minutes while the app runs (`calendar.poll_minutes`), on wake (a gap between ticks much longer than the interval), on demand
(`tt cal sync`, `--watch` keeps going). `Retry-After` of a 429 is honoured. A failing provider never raises into the UI: `tt cal status` shows
the last error; capture and the outbox are unaffected. Offline you see the last sync.

## Tokens
OS keyring entry `oauth:<provider>` (macOS Keychain, Windows Credential Manager, libsecret); the 0600 file fallback warns. Tokens never appear in the
outbox, the config, the personal store or any log (tests scan all of them). `tt cal disconnect` or `tt erase-local` removes them.

## Limits
Teams chat/presence, Apple/CalDAV (a generic CalDAV adapter would implement the same protocol), shared team calendars and an Outlook add-in are out of
scope. Google Meet attendance is not read. The Microsoft focus-time detection uses the "Focus time" category or subject (Graph v1.0 has no event type).
Provider fixtures in `packages/client_core/tests/fixtures/calendar` are hand-assembled from the API reference, **not recordings of a live account**;
nothing has been run against real Google or Microsoft services. `tools/smoke_calendar.py` is the script that does it with a test account
(runbook: `docs/uat/uat-personal-tracker.md`, "Live-account smoke"); it has itself only been run against fakes.

## Troubleshooting
| symptom | cause / fix |
|---|---|
| `no client id for google` | set `[calendar]` or the env var |
| `not connected: run tt cal connect` / `connect the calendar again` | token missing or revoked; connect again |
| a meeting is missing | it is declined/private/all-day/OOO (see `tt cal sync` "hidden"), or its calendar is excluded |
| `rate limited; retry after N s` | wait; the scheduler does |
| Microsoft consent screen says "need admin approval" | ask the tenant admin to approve the app (or drop `microsoft_attendance`) |
| browser sign-in never returns | port 8765 busy: set `redirect_port` and register that redirect |
| "no OS keyring available" warning | headless machine: tokens are in a 0600 file; use a desktop session for the keychain |
