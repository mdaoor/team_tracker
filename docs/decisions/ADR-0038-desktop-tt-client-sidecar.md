# ADR-0038: The desktop and mobile shells talk to `tt_client` through a Python sidecar
Date: 2026-10-24   Status: accepted (plan P11 "decision: ship `tt_client` as a Python sidecar binary built with PyInstaller for v1")

## Decision
The palette (React) never parses, validates, sends or queues. It calls a small `Bridge` (`apps/desktop/src/palette/types.ts`); in the Tauri
shell the bridge is a set of `tt_*` commands that run `tt-sidecar <command> <json>` (the CLI's runtime packaged with PyInstaller) and return
JSON. Tests use a scripted bridge, so the whole UI is covered without Tauri. A TypeScript port of `tt_client` is a later optimisation.

## Consequences
* One implementation of parsing, outbox, sync and manual-form rules (the CLI's). Files are byte-compatible across CLI, desktop, Slack.
* The Tauri shell (`src-tauri/`) is a scaffold: it has not been compiled here, and the installers/updater/tray/mobile targets are unproven.
  Acceptance items that need hardware or OS toolchains (6 s end-to-end with a local 7-8 B model, three-OS installers, mobile voice) are open.
* The sidecar entry point (`tt-sidecar`) is not written yet; the command list is fixed by the bridge: parse, pack, send, build-manual,
  outbox, resend, discard, open-editor, active.
