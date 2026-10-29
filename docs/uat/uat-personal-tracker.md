# UAT: personal tracker (Phase 15)

Needs the CLI (`uv run tt`) and, for the screens, `pnpm -C apps/desktop dev` then http://localhost:1420/demo.html (in-memory demo data, fixed clock Fri 23 Oct 2026 15:00).
Set `TT_CONFIG_DIR`/`TT_DATA_DIR` to a scratch folder and `TT_SECRETS=file` on a headless box.

1. **No server.** `tt add "every weekday 7:00 run 30m"`, `tt add "read 20m daily"`, `tt week`, `tt timer start --title reading`, `tt timer stop`, `tt month`.
   Expected: items appear with a repeat rule, minutes are recorded, `tt outbox` is empty, nothing was sent.
2. **Quick add.** `tt add --preview "Tue/Thu 18:00 Arabic course 1h"` shows `every Tuesday and Thursday at 18:00`.
3. **Calendar (needs your own OAuth client ids).** `tt cal connect google`, `tt cal sync`. Expected: next week's meetings, no declined/all-day/private ones, Teams join links shown.
   *Not run in the authoring environment: only fixtures were used.*
4. **Tray.** After a meeting ends (+2 min) `tt tray` lists it with a project cue; `tt tray send 1`; `tt sync`. Expected: a `log_time` with the note; sending the same meeting from a second laptop answers `deduplicated`.
5. **Series memory.** Send next week's instance of a recurring meeting to another project once; the following instance is suggested with that project (`✓ 95 %`).
6. **Development.** `tt add "course: Rust ch.3 1h"`, `tt item done <ref>`, `tt tray` is empty; `tt privacy --first-run --development`; the tray offers it; send. Expected: on the web console, project DEV, tag #development, visible to you and your line manager only.
7. **Personal.** `tt add "run 30m at 6pm"`; mark done; `tt tray send <ref>` is refused ("personal items are never sent").
8. **Unsend.** Send something, `tt sent unsend 1` within 10 minutes. Expected: before delivery the batch disappears; after delivery the time entry is removed (`time.unlogged` in the event feed).
9. **End of day.** `tt eod --send-all` then `tt eod --confirm` (logged in). Expected: the DailyLog is confirmed only if something was sent.
10. **Erase.** `tt erase-local --yes`. Expected: `personal.db`, the key and calendar tokens are gone.
11. **Screens.** In the demo page: keys N, Space, C, X, S, D/W/M, E, R, ?; Tab through a tray card; resize to 390 px (no horizontal scroll); first run via `?firstrun=1`.

12. **Calendars you can switch off** (needs a connected account, or the fake-backed tests as a rehearsal). `tt cal sync`, `tt cal list`, `tt cal exclude "<a calendar name>"`.
    Expected: that calendar's meetings leave `tt week` at once, `tt cal sync` no longer reads it; `tt cal include "<name>"` + `tt cal sync` brings them back.
    `tt cal suppress globex.com`: a finished meeting with someone @globex.com is still in `tt week` but not in `tt tray`; `tt cal suppress globex.com --remove` offers it again.
    `tt cal refresh` after sharing a new calendar with the account: it is listed ("1 added (Name)"). In the desktop app: **Calendars** -> the same switches, *Refresh the list*, the suppress list.
13. **A deleted meeting stays deleted.** Delete a synced meeting (`tt item delete <ref>`), then `tt cal sync --full`. Expected: it does not reappear and the sync reports it under "hidden: deleted locally".
    (Provider-side expiry of the sync cursor, HTTP 410, cannot be forced on a live account; it is covered by tests with the fake servers: `test_calendar_sync.py`.)
14. **Desktop shell** (Linux with the system libraries in `docs/client/desktop.md`, or macOS/Windows with the Tauri prerequisites; **unverified on a real desktop**): build the sidecar
    (`apps/desktop/sidecar/build.sh`), `pnpm --filter @tt/desktop tauri dev`. Expected: a tray icon with the timer line; closing the window leaves it running; `Ctrl/Cmd+Shift+Space` toggles the
    palette; finish a meeting (end time + 2 min) and within a minute an OS notification "<title> ended - Send 1 h to WEB?" appears once; after 17:30 on a workday with something done, one
    "How did today go?" notification; *Sync calendars now* in the tray menu syncs. Report what you see: the authoring environment had no tray host or notification daemon.
15. **Phone (Mobile Lite).** `pnpm --filter @tt/desktop build`, serve `apps/desktop/dist` from the same origin as the server (reverse proxy), open `/mobile.html` on the phone over HTTPS, *Add to
    Home screen*. Phone settings: handle, workspace, a capture token, project keys. Switch on airplane mode, reopen the app (it must open), add `work: fix login 30m p:web`, tap Done, send it from
    Ready to send ("1 waiting to send"), then go online: "Nothing waiting" within a minute and a 30-minute time entry on the web console. Check: no horizontal scroll at 390 px; "Phone settings" says the
    plan is not encrypted; repeating items answer "not available on the phone yet". **Not tried on a real device by the authors** (iOS: the page must be HTTPS and installed by hand).

## Live-account smoke (the owner runs this; it needs credentials the authors did not have)
`tools/smoke_calendar.py` talks to the **real** Google Calendar and Microsoft Graph APIs with a throw-away test account and prints a PASS/FAIL/SKIP table. Without credentials it prints
`skipped: no credentials` and exits 0, so it is safe in CI.

1. **Test accounts, never real calendars.** A Google account with a calendar named for the test, and a Microsoft 365 (work or developer-tenant) or personal Microsoft account.
2. **OAuth clients.** Google Cloud console: a project with the Calendar API enabled, an OAuth client of type *Desktop app* (consent screen "Testing", add the test account as a test user).
   Microsoft Entra: an app registration, *Mobile and desktop* public client, redirect `http://127.0.0.1:8765/callback`, delegated permissions `Calendars.ReadWrite` and `offline_access`
   (`Calendars.Read` is enough for the read half).
3. **One-time consent with write scope** (opens a browser, loopback redirect on port 8765):
   `GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... uv run python tools/smoke_calendar.py --connect google` and/or
   `MS_CLIENT_ID=... uv run python tools/smoke_calendar.py --connect microsoft`. It prints `export GOOGLE_REFRESH_TOKEN='...'` (or `MS_REFRESH_TOKEN`). Keep it out of shell history and git.
4. **Run:** `GOOGLE_CLIENT_ID=... GOOGLE_REFRESH_TOKEN=... [GOOGLE_CLIENT_SECRET=...] [GOOGLE_SMOKE_CALENDAR_ID=primary] MS_CLIENT_ID=... MS_REFRESH_TOKEN=... [MS_TENANT=common] uv run python tools/smoke_calendar.py`
   (`--only google|microsoft` to limit). Expected per provider, nine PASS rows: OAuth refresh, list calendars, read one window, incremental read, refresh with write scope, create test event,
   read it back, delete it, confirm it is gone. A token consented read-only gives SKIP (not FAIL) from "refresh with write scope" on. Exit 1 if anything failed.
5. **Cleanup.** The test event is titled `[tt-smoke] Team Tracker smoke test ...` and is deleted by the script; if "delete it" fails the table names the event id to remove by hand.
6. **What it does not cover** (do these by hand with `tt cal connect`): the interactive consent screen as a user sees it, a Microsoft tenant that requires admin consent, Teams join links and
   attendance, an expired sync cursor (410), rate limiting, and the OS keyring.
7. Report the table (it prints no tokens) in the PR or the UAT log. **The authors could not run it live**: no credentials were available; it has been run only against fakes
   (`packages/client_core/tests/test_smoke_calendar.py`, 10 tests).

Known limits: `docs/uat/known-limitations.md` (Phase 15 section).
