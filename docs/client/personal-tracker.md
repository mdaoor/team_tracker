# Personal time tracker (Phase 15)

The client is also a **personal planner and timer** that is useful with no server at all. Plan your day, week and month, repeat training or
sport, run a timer, pull meetings from your calendars. Nothing leaves the device unless you send it; sending is one tap.
Decisions: ADR-0053 (local-first store), ADR-0054 (calendar dedup, `corrects`), ADR-0055 (development project).

## Model (`tt_client.personal`)
| piece | what it is |
|---|---|
| `PersonalItem` | title, `kind` (task, meeting, focus, training, course, reading, sport, habit, errand, other), `category` (work, meeting, development, personal), time or "anytime", `rrule`, status (planned, active, done, skipped, cancelled), tags, `project_hint`, `share_policy`, `sent_refs`, `calendar_ref`, attendees, join link |
| `Timer` | one per item; **one running at a time**, starting another pauses the first (the server's rule); segments are kept |
| `Rule` | match (kind, title regex, attendee domain, calendar, keyword, series, attendee set) -> set (category, project, share, tags); user or learned |
| store | encrypted SQLite, own migrations, JSON export/import, "erase all local data" |

All times are stored as UTC instants and shown in your zone. Recurrence is expanded on the local wall clock, so "weekdays 7:00" stays 7:00 across
daylight-saving changes (a wall time that does not exist that day moves forward by the gap).

## Categories carry the sharing default
| category | default | control |
|---|---|---|
| work | ask once, then remember | `tt privacy --work ask|auto|never` |
| meeting | suggested in the tray after each meeting | `--meeting ask|auto|never` |
| development (training, courses, reading) | **off**; one toggle sends it as `log_time` on the development project tagged `#development` | `--development` ; per series "send each completed instance" |
| personal (sport, errands, health) | **never**: there is no send path, no setting and no sidecar command that sends it | none |

The server cannot tell a forged "personal" from anything else; the guarantee is that this client offers no way.

## Quick add (one line)
`run 30m at 6pm` · `9-10 reading` · `course: Rust ch.3 1h` · `tomorrow 3pm call with @sara 30m` · `p:WEB fix login 2h`.
Recurring: `every weekday 7:00 run 30m`, `Tue/Thu 18:00 Arabic course 1h`, `read 20m daily`, `every 2 weeks on friday 15:00 retro 1h`,
`... for 4 weeks`, `... until dec 31`, `... x10`. No date and no time means today, anytime. A line the parser cannot place is **personal** until
you or a tag/label/project says otherwise. A local model (gateway `/v1/translate/personal`, local models only) may only make a line more
private on its own; a less private reading is a question ("looks like work?"). Numbers and dates are always the parser's.

## Ready to send
At `end + 2 min` (app running) or at the next launch a finished meeting appears with a project suggestion and the usual cue
(`✓` 80 %+, `suggested` 60 %+, `ask`). `[Send]` queues `log_time` (+ the note "Sprint planning with @sara, @ali") in the ordinary outbox with
`source.origin` (`personal`, `calendar:google`, `calendar:microsoft`) and `calendar_ref{provider,event_id,series_id,instance_start}`. Only ids
and your note go; titles, attendees and links stay. Suggestions come from, in order: recurring series memory, rules, attendee domain,
glossary/aliases, attendee team, iteration ceremonies. Accepting teaches a rule; `[Not work]` teaches "never offer this". If no project is
known you get three chips from the pack. The same occurrence sent twice (two laptops, a retry) answers `deduplicated`.
**Unsend** within 10 minutes: an undelivered batch is discarded, a delivered one gets a correction (`update` + `corrects`) that removes the time entry.

## Reviews
End of day: hours by category, items done, unsent work with `[Send all] [Pick] [Skip]`, then confirm the server DailyLog (only if something was sent).
Weekly: planned vs done, development hours, meeting load, habit streaks, optional development summary note.

## Where it lives
* CLI: `tt day|week|month|add|timer|cal|tray|recur|item|eod|review|privacy|sent|erase-local|export|import`.
* Desktop/mobile React views: `apps/desktop/src/personal` (Today/Week/Month, tray, end-of-day and weekly cards, recurring editor, first-run privacy,
  keyboard map **N** new, **Space** timer, **C** done, **X** skip, **S** send, **D/W/M** views, **E**/**R** reviews, **?** help), driven through the
  sidecar (`tt sidecar <command> '<json>'`, Tauri command `tt_personal`). Mobile uses the same views at 390 px. **The Tauri shells (desktop tray,
  notifications, mobile share sheet) are scaffolds and have not been built or run here.**
* Privacy: `tt privacy`, `tt sent` (audit with server keys), `tt erase-local`.

## Encryption and keys
Fernet (AES-128-CBC + HMAC) per row; key in the OS keyring (`keyring`), or `~/.local/share/tt/secrets.json` mode 0600 with a warning on
headless machines (`TT_SECRETS=file|keyring` forces). Readable on disk: ids, the UTC date an item starts, series id. Losing the key means losing
the store: keep an export.
