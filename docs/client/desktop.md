# Desktop and mobile palette (phase 11 §1-3, 5)

`apps/desktop` is the React UI shared by the desktop (Tauri) and mobile shells.

| Piece | File | Behaviour |
| --- | --- | --- |
| Palette input | `palette/PaletteInput.tsx` | 300 ms debounce; the structured parse shows at once, the model's replaces it; `Enter` sends without waiting; `Esc` discards; `Tab` edits rows; `⌘/Ctrl+J` Jira (only when the project has Jira); `⌘/Ctrl+M` manual form; `⌘/Ctrl+E` open the JSON in the editor and re-validate; first-run ghost examples |
| Preview rows | `PreviewRows.tsx` | Task / Project / Time / Plan / Tags / Stage with `✓ matched`, `72 %`, `suggested`, `ask` |
| Manual form | `ManualForm.tsx`, `manual.ts` | choices are exactly the pack's lists; mandatory fields per project from `pack.manual_mode`; works offline; presets 15/30/60/120; Open as JSON, Save draft to outbox |
| Outbox | `OutboxList.tsx` | pending/sent/failed, edit/resend/delete, Jira key |
| Tray timer | `timerTray.ts` | server's active activity when online, else the last local start; starting another item pauses the first |
| Bridge | `types.ts`, `sidecar.ts` | the only way to parse/send/queue (ADR-0038) |

Tests: `pnpm --filter @tt/desktop test` (13, scripted bridge). Run the UI in a browser against a stub bridge with `pnpm --filter @tt/desktop dev`.

**Not done here (needs toolchains/hardware):** compiling `src-tauri` (scaffold only), running the PyInstaller build of `tt-sidecar` (spec and script exist, never run in CI), signed
installers for three OSs, the updater, tray icon, notifications, iOS/Android builds, voice-to-text and the share sheet, and the 6 s
end-to-end measurement with a local model. These stay open acceptance items for phase 11.

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
