# Desktop shell and palette (phase 11 §1-3, 5; phase 15 shell)

`apps/desktop` is the React UI shared by the desktop (Tauri) shell and the phone app ("Mobile Lite", a PWA: `docs/client/personal-tracker.md#mobile`).
There is **no Tauri mobile (iOS/Android) shell**: see that section for what the phone has instead and why.

| Piece | File | Behaviour |
| --- | --- | --- |
| Palette input | `palette/PaletteInput.tsx` | 300 ms debounce; the structured parse shows at once, the model's replaces it; `Enter` sends without waiting; `Esc` discards; `Tab` edits rows; `⌘/Ctrl+J` Jira (only when the project has Jira); `⌘/Ctrl+M` manual form; `⌘/Ctrl+E` open the JSON in the editor and re-validate; first-run ghost examples |
| Preview rows | `PreviewRows.tsx` | Task / Project / Time / Plan / Tags / Stage with `✓ matched`, `72 %`, `suggested`, `ask` |
| Manual form | `ManualForm.tsx`, `manual.ts` | choices are exactly the pack's lists; mandatory fields per project from `pack.manual_mode`; works offline; presets 15/30/60/120; Open as JSON, Save draft to outbox |
| Outbox | `OutboxList.tsx` | pending/sent/failed, edit/resend/delete, Jira key |
| Tray timer | `timerTray.ts` | server's active activity when online, else the last local start; starting another item pauses the first |
| Bridge | `types.ts`, `sidecar.ts` | the only way to parse/send/queue (ADR-0038) |

Tests: `pnpm --filter @tt/desktop test` (13, scripted bridge). Run the UI in a browser against a stub bridge with `pnpm --filter @tt/desktop dev`.

## State of the native shell (verified 2026-10-28, Linux x86_64 only)
| Thing | Result |
|---|---|
| `cargo check` in `apps/desktop/src-tauri` | **passes** (Tauri 2.12, crates fetched from crates.io through the proxy). It first failed on missing system libraries, see below. |
| `cargo clippy --all-targets`, `cargo fmt --check` | clean |
| `cargo test` | 3 passed (`src/notice.rs`: parsing of the sidecar's notification, timer and calendar answers) |
| `cargo build` (debug) and a launch under `xvfb-run` + `dbus-run-session` for 40-60 s | starts, no panic; the background loop's first step ran 5 s after launch and consumed a "ready to send" notification from a seeded store (a second sidecar call right after returned none), so shell -> frozen sidecar -> Python works end to end on Linux |
| Tray icon visible, the OS notification actually drawn, window show/hide, close-to-tray, the global hotkey | **not verified**: no system tray host, notification daemon or real display in the authoring environment |
| `tauri build` (installer/bundle), signing, updater | **not attempted** |
| macOS, Windows | **not built or run**; keyring behaviour, tray and notification code paths there are untested |
| iOS / Android (Tauri mobile) | **not started**: needs the mobile toolchains *and* a way to run the Python engine on the phone (see Mobile Lite) |

**System libraries for `cargo check` on Ubuntu 24.04** (what was missing and installed): `libwebkit2gtk-4.1-dev` (pulls GTK 3, `gdk-3.0`, libsoup 3,
JavaScriptCore), `libayatana-appindicator3-dev` (tray), `librsvg2-dev`, `libxdo-dev`, `libssl-dev`, `build-essential`, `pkg-config`.
Two files must also exist before the build script runs: `src-tauri/binaries/tt-sidecar-<target triple>` (Tauri checks `externalBin`; build it with
`apps/desktop/sidecar/build.sh`) and `src-tauri/icons/icon.png` (the committed one is a **placeholder** clock glyph; real artwork and the
`.icns`/`.ico` set for installers are still needed).

### What the shell does (`src-tauri/src/shell.rs`)
* **Tray**: a menu with the running timer line (updated each minute), *Open My time*, *Quick add* (toggles the palette), *Sync calendars now*, *Quit*;
  left click opens "My time". Closing the window hides it, so the timer, calendar sync and nudges keep running; *Quit* in the tray ends the app.
* **Global hotkey** `CommandOrControl+Shift+Space` toggles the palette window (the scaffold registered the plugin but no shortcut).
* **Background loop**, one sidecar call per minute: `personal_shell_tick` = due calendar syncs (the Python `Scheduler` decides which: first sync,
  poll interval, wake, `Retry-After`) + notifications not yet shown + the running timer. One call, because the frozen binary pays its start-up
  cost per process (below).
* **OS notifications** through the `NotificationPort` (`tt_client.notify`): the policy is Python (`tt_client.personal.notify`: *"Sprint planning ended: send 1 h to WEB?"*
  once per finished meeting or done item; *"How did today go?"* once per workday after `notify.eod_time`, default 17:30, empty = off); the sidecar's
  `CollectingNotifier` returns them and Rust shows each with `tauri-plugin-notification`. Desktop notifications carry no click action: the tray
  menu and window are the way in.
* Rust holds no business logic and no secrets; `capabilities/default.json` grants only core, notification and one global-shortcut permission.

### The frozen sidecar (PyInstaller), measured
`apps/desktop/sidecar/build.sh` (`uv run --with pyinstaller`, spec `tt-sidecar.spec`) **works** on Linux x86_64 / Python 3.13: about 3 minutes,
**131,050,456 bytes (125 MiB)** single-file ELF, the script's own smoke test passes (`--verbs` answers 63 verbs), and `personal_privacy`,
`--serve` with `personal_add` + `personal_day` return real JSON from the frozen binary. Only harmless warnings (Windows `ctypes` libraries, two optional
database drivers). Costs to know: **a cold start is about 3.6 s** (one-file unpack on every spawn; measured on this machine), which is why the
background loop makes one call per minute and why the next step is a long-lived `tt-sidecar --serve` child (the protocol exists and is tested) or a
one-directory bundle; 125 MiB is large (a `--onedir` build with `cryptography`, `dateparser` data trimmed would be smaller). The frozen binary must be
rebuilt whenever a verb is added: a stale binary answers `unknown verb` (the first shell run here hit exactly that).

### Still open for phase 11 / 15
Signed installers for three OSs, the updater, real tray/notification verification on each OS, `--serve` instead of a process per call, the 6 s end-to-end
measurement with a local model, voice-to-text and the share sheet (mobile only).

## Management commands (phase 14)

`palette/commands/` adds the role switch, verb chips, the command card with its Effect preview and two-step confirmation for destructive
commands, forms generated from the catalogue (`SchemaForm`), and the My queue / Team now panels. They talk to the sidecar through
`commandBridge` (`cmd-*` verbs implemented in `tt_client.sidecar_commands`; the Tauri scaffold forwards them like the capture verbs). A
reporter with no other role sees none of it. Tests: `palette/commands/commands.test.tsx` (16, including axe over header, card, destructive
confirmation, form and panels). See `docs/client/management-commands.md`.

## The `tt-sidecar` executable (phase 13 finish)

`tt-sidecar` is a console script of `apps/cli` (`tt_cli/sidecar.py`) and the entry point PyInstaller freezes. One executable serves every
shell verb; the Rust side only forwards (`tauri::command` -> `tt-sidecar <verb> '<json>'`).

| Invocation | Behaviour |
| --- | --- |
| `tt-sidecar <verb> '<json args>'` | one request, one JSON document on stdout, exit 0 (handled failures are `{"error": ...}`) |
| `tt-sidecar <verb>` | the JSON args are read from stdin |
| `tt-sidecar --serve` | long-lived: `{"verb","args"}` per stdin line, one JSON answer per stdout line (no process start per keystroke) |
| `tt-sidecar --verbs` | the verb list; an unreadable request exits 2 with a JSON error |

Verbs: capture (`parse pack send build-manual outbox resend discard open-editor active`, the `Bridge` in `palette/types.ts`), `cmd-*`
(`tt_client.sidecar_commands`) and `personal_*` (`tt_client.personal.bridge`). stdout carries only JSON: stray prints are diverted to
stderr and an unexpected exception still answers `{"error": "..."}`. It works with no login and no server (personal verbs; `send` writes the
outbox first and answers `queued`; `cmd-*` run from the cached catalogue and `me`), and enforces the destructive-command confirmation itself.

Tests: `apps/cli/tests/test_sidecar.py` spawns the real console script. Build the frozen binary per OS with `apps/desktop/sidecar/build.sh`
(spec: `apps/desktop/sidecar/tt-sidecar.spec`; output `src-tauri/binaries/tt-sidecar-<target triple>`, which `tauri.conf.json` `externalBin`
expects; it ends with a smoke test of the frozen binary). Not run in CI: it needs PyInstaller and a per-OS runner.
