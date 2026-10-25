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

**Not done here (needs toolchains/hardware):** compiling `src-tauri` (scaffold only), the `tt-sidecar` PyInstaller entry point, signed
installers for three OSs, the updater, tray icon, notifications, iOS/Android builds, voice-to-text and the share sheet, and the 6 s
end-to-end measurement with a local model. These stay open acceptance items for phase 11.

## Management commands (phase 14)

`palette/commands/` adds the role switch, verb chips, the command card with its Effect preview and two-step confirmation for destructive
commands, forms generated from the catalogue (`SchemaForm`), and the My queue / Team now panels. They talk to the sidecar through
`commandBridge` (`cmd-*` verbs implemented in `tt_client.sidecar_commands`; the Tauri scaffold forwards them like the capture verbs). A
reporter with no other role sees none of it. Tests: `palette/commands/commands.test.tsx` (16, including axe over header, card, destructive
confirmation, form and panels). See `docs/client/management-commands.md`.
