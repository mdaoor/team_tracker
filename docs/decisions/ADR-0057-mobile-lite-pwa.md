# ADR-0057: The phone gets "Mobile Lite", an installable web app, not a Tauri mobile app
Date: 2026-10-28   Status: accepted (plan P15 §15.2 asked for a mobile shell; PR #11 review part 3 finding 6: "mobile does not exist")

## Context
The personal tracker's logic (one-line parser, RRULE expansion across DST, rules and suggestions, the encrypted store, calendar adapters, the send
path) is Python in `tt_client` and runs behind the desktop shell as a PyInstaller sidecar (ADR-0038). A Tauri 2 iOS/Android app cannot do that:
iOS forbids spawning a bundled executable, and a Python runtime on Android (Chaquopy, Pyodide in a webview) is a project of its own, with the
native keyring, `cryptography`, `dateparser` and `sqlite3` to carry. The mobile toolchains (Xcode, Android SDK/NDK) are also not available here.
What we could do cheaply and honestly is run the *React views we already have* in a phone browser, installed to the home screen.

## Decision
1. **Mobile Lite** = `apps/desktop/mobile.html` + `src/mobile/` + `public/manifest.webmanifest` + `public/sw.js`. The unchanged `PersonalApp` views
   run against `localBridge`, a TypeScript `PersonalBridge` kept in `localStorage`, instead of the Python sidecar.
2. **Scope is deliberately small and stated**: add one-liners with a small grammar, timer, Today/Week/Month, Ready to send with project chips from a
   configured list, an offline outbox delivered to `POST /api/v1/w/<ws>/capture/batches` (the same `ClientBatch` `log_time` message the desktop
   sends, `source.origin = personal`), end-of-day and weekly numbers. **Not** repeating items, calendars, suggestions/rules, the full parser,
   daily-log confirmation, taking back a delivered send, push notifications. Every missing feature answers `not_on_mobile` with where to do it.
3. **The service worker caches the shell only** (page, hashed assets named by the page, icons, manifest). It never caches or touches `/api/`; data
   lives in `localStorage` and sends in the outbox, so a phone offline all day loses nothing.
4. **Privacy trade-off, accepted and shown to the person**: the phone's store is **not encrypted** and the API token is kept in clear in the same
   browser storage (ADR-0053's per-row encryption needs a key store the web does not have). The Phone settings dialog says so. Personal items still
   have no send path. The token should be a capture-scoped token the person can revoke.
5. **Deployment**: serve `dist/` from the same origin as the API (the server has no CORS and this ADR does not add any); `TT_BASE=/m/` builds under a prefix.
6. A **native** mobile app stays open. Its realistic routes, for a later ADR: (a) port the planner core (parser, recurrence, tray rules) to TypeScript so
   one engine serves PWA and Tauri mobile; (b) Pyodide in the webview; (c) a thin native shell loading this PWA. (a) is the likely one, and the
   shape contract (`contract.json`, checked on both sides) is what keeps a second engine honest.

## Consequences
* A phone can capture and send in two taps today, offline, without a store listing or a mobile toolchain. Cost: a second, smaller implementation of
  the bridge (about 1,000 lines with the shell + tests) that can drift from the Python one; the contract test and `not_on_mobile` boundaries limit that.
* iOS install is manual ("Add to Home Screen" over HTTPS); no install prompt, no background sync, no push there. Android Chrome installs from the prompt.
* Verified in Chromium only (`e2e/mobile.spec.ts`, production build): manifest valid, the worker controls the page, reload offline works, an offline send is
  delivered on reconnect. **Not tried on a real device.**

## Alternatives considered
* Tauri mobile with the Python sidecar: not possible on iOS, unproven on Android; blocked here by toolchains.
* Reusing `demoBridge`: it fakes the clock, timer and suggestions; making it real would mean rewriting it anyway.
* A mobile page that talks to the desktop sidecar over the LAN: exposes personal data on the network and needs auth and TLS.
* Server-side personal items: contradicts "the server sees only what is sent" (ADR-0053).
