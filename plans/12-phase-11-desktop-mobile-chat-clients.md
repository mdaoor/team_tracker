# Phase 11 — Reporter shells: desktop palette, Slack/Teams bot, mobile

## Goal
Ship the reporter‑facing shells on top of `tt_client` (Phase 2) with the **two sending modes** (Assisted free‑text → LLM → JSON file → server [+ Jira]; Manual form from an imported context pack) available in every shell. The desktop quick‑capture palette is primary (UX §2.1).

## Depends on
Phase 2 (client core, both modes), Phase 1 (capture API). Jira chip needs P10; notifications need P8 for violation pings (optional).

## Inputs
- UX §2.1, §2.2 (palette mock), §2.6 (micro‑interactions), §7.1
- Phase 2 §2.6 (modes), `ClientBatch` file format

## Scope
1. **Desktop (Tauri v2 + React)**: global hotkey → single‑line palette; live parse preview (300 ms debounce) with confidence cues; `Enter` send, `Tab` edit row, `Esc` discard, `⌘J` Jira toggle, `⌘M` switch to Manual form, `⌘E` open the JSON file in the editor; tray icon with active item + elapsed time, menu `pause · done · switch`; offline queue; end‑of‑day notification → DailyLog confirm dialog; first‑run ghost examples; settings (server, token, LLM provider/model, default mode, hotkey).
2. **Manual form (desktop)**: side panel/form with fields and pickers fed by the pack; mandatory fields from `pack.manual_mode.required_fields`; tags multi‑select with provisional free entry; time picker (`hh:mm`, presets 15/30/60/120); date picker with natural text; "Open as JSON" / "Send" / "Save draft to outbox".
3. **Outbox view (desktop)**: list of JSON files (pending/sent/failed), open/edit/resend/delete, shows server response and Jira key.
4. **Slack bot** (Bolt for Python): DM free text → parsed card as a Slack message with buttons `Send · Edit · Discard · Manual…`; `Manual…` opens a Slack modal form (Block Kit) built from the pack (same mandatory rules); slash commands `/tt now`, `/tt today`; end‑of‑day DM; violation notifications (P8 `NotificationPort` Slack impl); account linking via `tt login` deep link.
5. **Mobile shell**: Tauri mobile (iOS/Android) build of the same React palette + form, voice‑to‑text via platform API, offline queue; share‑sheet target for URLs → `evidence`.
6. **Teams bot**: thin adapter reusing the Slack bot's card model (stretch).

## Out of scope
Jira credentials on devices (never), model hosting itself: the desktop app bundles the Phase 2b gateway in `local_only` mode as an optional sidecar (talking to the user's Ollama) or points at a team/cloud gateway URL from settings.

## Detailed tasks
- Shared React package `apps/desktop/src/palette/` (also used by mobile): `PaletteInput`, `PreviewRows` (Task/Project/Time/Plan/Tags/Stage with ✓/%/suggested/ask), `ModeSwitch`, `ManualForm`, `OutboxList`, `TimerTray` state; talks to `tt_client` through a Tauri command bridge (Python sidecar via `tauri-plugin-shell` or port `tt_client` to TypeScript — **decision: ship `tt_client` as a Python sidecar binary built with PyInstaller for v1**, ADR‑0016; a TS port is a later optimisation).
- Palette parse loop: keystroke → debounce → sidecar `parse(text)` (structured parser immediately, LLM result replaces rows when it arrives; show spinner on rows not yet confirmed); never block `Enter`.
- JSON ownership: `Enter` writes the outbox file then sends; `⌘E` opens the file in the OS default editor and watches it; on save → re‑validate → card updates; `Send`.
- Manual form validation: mandatory fields per pack; a field marked required but missing shows inline help; the form never calls the server to validate (works offline).
- Tray timer: from the server's active activity when online, else from the local last `start`; starting another item auto‑pauses (server rule; UI mirrors).
- End of day: one notification at the configured time (person's zone); dialog with editable summary + `Confirm`; dismiss never re‑nags (UX §2.6).
- Updates: Tauri updater; signed builds; CI matrix macOS/Windows/Linux.
- Slack: card = Block Kit section per preview row; `Edit` opens the manual modal pre‑filled; `Send` posts the batch with the Slack user's linked token; errors as ephemeral messages; threads keep the JSON as a snippet (the "owned JSON file" equivalent in chat).
- Tests: Playwright for Tauri (webview) covering: assisted send, edit JSON then send, manual form mandatory enforcement, offline queue drain; Slack bot tests with Bolt's test harness; snapshot tests for Block Kit payloads.

## Acceptance criteria
- [ ] UX §7.1 completes in ≤ 6 s wall time with a local 7‑8 B model on a laptop GPU (measure and record).
- [ ] Both modes available in desktop, Slack and mobile; the produced `ClientBatch` files are byte‑compatible with the CLI's (`tt send` can send a file created by the desktop app and vice versa).
- [ ] Manual form refuses to send with a missing mandatory field and shows which; the server still accepts any valid file sent by other means (confirming the client‑side nature of the rule).
- [ ] Offline: 20 messages queued over 10 min, all delivered once on reconnect; the tray timer stays correct.
- [ ] End‑of‑day flow confirms the DailyLog from the notification in two clicks.
- [ ] Slack `Manual…` modal lists exactly the pack's projects/tags/people.
- [ ] Jira chip appears only for projects with Jira configured; `⌘J` toggles `jira.send`.

## Exit checklist
- [ ] Installers for three desktop OSs in CI artifacts; `docs/client/desktop.md`, `docs/client/slack.md`.
- [ ] ADR‑0016 sidecar decision; ADR‑0017 "JSON outbox file is the interchange format between shells".
