# UX addendum: the personal tracker (Phase 15)

Extends `team_tracker_ux.md` §2 (the reporter's effort is one line or one tap). Text mocks; the real screens are `apps/desktop/src/personal`.

## Today
```
My time  [Today] [Week] [Month]  ‹ Today ›        Repeat…  End of day  Weekly review  Privacy  ?
[ Add: run 30m at 6pm · course: Rust ch.3 1h · every weekday 7:00 run 30m                      ]
  → run · Personal · Fri 18:00 · 30 min

Fri 23 October 2026
Planned 6 h 35 of 6 h 24 (103 %)  [██████████████████]           Ready to send (3)
07:00–07:30  run            ● Personal 30 min ↻ repeats   [Reopen]     ┌ Send 30 min Client sync with Globex → DATA?
09:00–10:00  Sprint planning ◆ Meeting 1 h Outlook Join                │ ✓ 80 % · “Client sync with Globex with @jo”
11:00–11:30  Client sync …   ◆ Meeting 30 min Google Calendar          │ [Send] [Edit] [Not work]
12:00–12:45  Offsite …       ◆ Meeting 45 min                          ├ Send 45 min Offsite logistics?   Which project?
15:30–17:00  fix login       ■ Work 1 h 30   [Start timer][Done][Skip] │ [WEB] [DATA] [OPS]   [Edit] [Not work]
Anytime today: read ▲ Development 20 min ↻   Rust ch.3 ▲ 1 h (done)   └ …
Assigned to me in Team Tracker: WEB-142 fix the login redirect loop (from the server)
```
Glyphs repeat the category colour (■ work, ◆ meeting, ▲ development, ● personal). A running timer shows a bar under the header with [Stop timer].

## Week
```
Week of Mon 19 Oct · planned 21 h of 32 h
Mon 19 [██░░] 3 h │ Tue 20 │ Wed 21 │ Thu 22 │ Fri 23 [██████] │ Sat 24 (0 h capacity) │ Sun 25
 ■ fix login  ↻ run 07:00   [Move to… ▾]   (drag onto another day; meetings from calendars cannot be moved)
Work 12 h  Meeting 5 h  Development 3 h  Personal 1 h 30
```
At 390 px the seven columns stack into one list.

## Month
Calendar grid, one button per day (`Fri 23 Oct: Meeting 1 h 45, Work 1 h 30; sent 1 h`), glyph dots per category, hours per category above, and a
bar sparkline "sent vs private" with a text alternative.

## Tray card
`Send 1 h Sprint planning → WEB?` · `Meeting · 09:00 · 1 h · 70 % suggested · attendees are on team web-squad` · `“Sprint planning with @sara, @ali”`
`[Send] [Edit] [Not work]`. Unknown project: no [Send]; three chips `[WEB] [DATA] [OPS]`, one tap sends. After a send the status line says
`Sent 1 h Sprint planning → WEB` with [Unsend] (10 minutes). Development entries say `· #development`.

## End-of-day card
```
End of day · Fri 23 October 2026
Category     Done   Planned          Not sent yet (3)
Work         1.5 h  1.5 h            ☐ Send 1 h Sprint planning → WEB?
Meeting      2.3 h  2.3 h            ☐ Send 30 min Client sync … → DATA?
…                                    ☐ Send 45 min Offsite logistics?  (needs a project: choose it in the tray)
3 done · 2 still open · 0 sent
[Send all] [Pick] [Skip]        after sending:  [Confirm daily log]
```
## Weekly review
Planned vs done meter, development hours with the items, meetings hours and % of capacity, streaks (`run: 4 in a row (9 of 10)`),
checkbox "Share a development summary with my manager" -> `[Share and close]`.

## First run
"Your personal items stay on this device. Meetings and work you choose to send go to Team Tracker." Work: ask / send automatically / never.
Meetings: same. Development: checkbox "Send development time to my development project, tagged #development". "Personal: never sent. There is no
way to send a personal item." `[Got it]`.

## Keys
N new · Space timer · C done · X skip · S send · D/W/M views · E end of day · R weekly review · ↑↓ move · ? help · Esc close.
