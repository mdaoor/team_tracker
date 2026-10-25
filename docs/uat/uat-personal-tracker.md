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

Known limits: `docs/uat/known-limitations.md` (Phase 15 section).
