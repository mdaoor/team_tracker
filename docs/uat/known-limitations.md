# Known limitations and open questions (as of this build)

## Not verified in the authoring environment
* **Real LLM quality.** All gateway behaviour is tested with scripted models. The real-model golden run is wired (`TT_LLM_REAL=1 pytest -m llm apps/llm_gateway/gateway_tests/test_real_model_golden.py`) but
  has **not been run**: no model or key was available. Treat summaries, suggestions and "Generate with AI" quality as unmeasured.
* **Desktop (Tauri) and mobile shells** are scaffolds; the palette UI is tested, the native shell, tray, installers, updater and iOS/Android builds are not built.
* **Slack bot transport** has not been run against a real Slack workspace (the card/modal logic is tested).
* **Helm chart** was written without `helm`/`kind` available: run `helm lint` and the smoke test in `docs/ops/helm.md` first.
* **Real Jira, real Active Directory, real OIDC provider** were exercised only against fakes / throw-away servers (TLS to Postgres and LDAPS StartTLS use real servers in tests).
* **Browsers other than Chromium** for the web console and decks.

## Performance targets
Plan budgets: capture < 150 ms/message, `query/items` < 300 ms, snapshot < 2 s cold. Measured on the seed-sized data and on the load fixture
(see `docs/ops/load-report.md`); the snapshot cold run does **not** meet 2 s on large data - mitigation and issue are recorded there.

## Deviations (each has an ADR)
`docs/decisions/ADR-0016` ... `ADR-0039`. Notable: projection rebuild is a *verification* tool, not a replay (ADR-0040); Kerberos is proxy-terminated (ADR-0036).

## Open product questions
1. Kerberos: is proxy-terminated SSO acceptable, or is in-process GSSAPI required? (ADR-0036)
2. External packs: default **off** for downloads, **on** for in-product "Generate with AI" when a gateway exists. Confirm.
3. Share-token redaction: `standard` by default (no wellbeing/habits/live sessions); `strict` additionally pseudonymises people. Confirm the default.
4. Should admins get an *audited override* of the Inbox privacy rule (today: private even from admins, ADR-0024)?

## Phase 15 (personal tracker)
* Calendar adapters were exercised only against hand-assembled fixtures (Google v3 and Graph v1.0 shapes); no live Google or Microsoft account, no real consent screen, no admin-consent case.
* OS keyring behaviour on macOS, Windows and Linux desktops is not verified (a fake backend and the 0600 file fallback are tested).
* The Tauri desktop/mobile shells (tray timer, notifications "meeting ended - send?", share sheet, background sync, installers) are scaffolds that were not compiled; the React views, the sidecar commands and the CLI are tested. `tt-sidecar` as a PyInstaller binary is still not built.
* The development project is visible to workspace admins (platform rule for private items), not only to the person and their line manager (ADR-0055).
* A meeting moved on the calendar after it was sent can be logged a second time (new `instance_start`); Unsend the first.
* Write-back of focus blocks to calendars is not implemented.
