# ADR-0058: Desktop shell: tray, OS notifications and background calendar sync live in Rust but decide nothing
Date: 2026-10-28   Status: accepted (plan P15 §15.2/15.3; PR #11 review part 3 finding 5; handover 14 L7)

## Context
The Tauri scaffold forwarded sidecar verbs and registered a global-shortcut plugin, nothing more: no tray, no notifications, no sync while the window
was closed. The review's rule is that Rust holds no business logic (ADR-0038). Notifications already had a port (`tt_client.notify.NotificationPort`,
Phase 13 R6.7) with a console adapter and a once-only log.

## Decision
1. **Policy in Python, display in Rust.** `tt_client.personal.notify.notify_due(tracker, port)` decides what to say: one "ready to send" notice per
   finished meeting or done item (the existing `tray.notified` memory) and one end-of-day nudge per workday after `notify.eod_time`
   (default 17:30, empty = off) when something was done or waits. A `CollectingNotifier` (a `NotificationPort`) returns them as JSON via the sidecar
   verb `personal_notify`; the Rust side only shows them (`tauri-plugin-notification`).
2. **One background call per minute**, `personal_shell_tick`: due calendar syncs (the existing `Scheduler`: first sync, `calendar.poll_minutes`, wake,
   `Retry-After`), new notifications and the running timer. A single verb because the frozen sidecar costs about 3.6 s to start; a long-lived
   `--serve` child is the next step and needs no change to the verbs.
3. **Tray** with the timer line, open, quick add, sync now, quit; **closing the window hides it** so the loop keeps running; **the palette hotkey** is wired.
4. Notification clicks are not handled (desktop notifications have no action callback in the plugin); the tray menu is the way in. The end-of-day nudge
   opens nothing by itself.
5. The Rust crate keeps pure parsing in `notice.rs` with unit tests (`cargo test`), and `capabilities/default.json` grants the minimum.

## Consequences
* Compiles and starts on Linux (verified), and the whole shell -> sidecar -> Python path runs; the visible parts (tray icon, drawn notification,
  hotkey) are unverified, and macOS/Windows are untested.
* The sidecar binary must be rebuilt when a verb is added; a stale one answers `unknown verb` and the shell logs it and carries on.
* The placeholder icon must be replaced before any installer is built.

## Alternatives considered
* Scheduling in Rust (a timer per nudge): duplicates the Python rules and cannot be tested with the Python clock fakes.
* Notifying from the webview with the Notification API: the window is hidden to the tray, the page is not running.
* One sidecar call per concern per minute: three cold starts a minute.
