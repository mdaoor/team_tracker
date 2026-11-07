# Changelog

Format: [Keep a Changelog](https://keepachangelog.com), versions follow `docs/ops/versioning.md`.

## [1.0.0-rc1] - unreleased
### Added
* Phase 14 (client half): `ClientCommand` and `ClientBatch.commands` (schema 0.2, ADR-0051/0052), command catalogue cache, role modes (`tt role`), `tt do` (assisted, deterministic parser + gateway `/v1/translate/command`), `tt cmd` (forms generated from JSON Schema), effect previews, destructive confirmation (`--yes` is not enough, `--force` is logged), `tt queue / team now / approve / waive / ops`, desktop role switch + command card + panels, Slack `/tt do` + approval/waive buttons, `docs/client/management-commands.md`.
* Phases 0-10 (capture, overlay, access control, web console, query/views, snapshot/presentations/insights, governance, governance UI, Jira projection).
* Review packages A-I (see `docs/reviews/`): security fixes, access-control completeness, ADR-0016..0040, top-down plan API and linkage, plan slide kinds and plan-only rule,
  presentation templates as data, external-LLM generation pack/import and "Generate with AI", item companions (comments, checklists, links, custom fields, attachments),
  recurrence rules, progress recompute, roadmap editor, policy editors with version history, habit sparklines, accessibility audit (axe = 100 on 14 screens), component stories,
  OpenAPI contract tests, Kerberos-behind-proxy SSO, Postgres `verify-full`, certificate hot-reload.
* Phase 11 shells: Slack bot logic, desktop/mobile palette package (Tauri shell scaffolded, not built).
* Phase 12: rate limiting, security headers/CSP, Prometheus metrics + Grafana/alert rules, retention purge, GDPR export/erase, `verify-projection`, backup/restore drill scripts,
  systemd units, Helm chart (unverified), config reference generated from `Settings`, threat model, runbooks, guides.
* Phase 14 (server): management commands (`GET /commands/catalogue`, `POST /commands`, commands inside `/capture/batches`, `POST /items:merge`) with 45 ops over the same services as the web console, `item.merge`/`item.split`, role presets (scrum master, coach, project manager) and role modes in `GET /me`, ContextPack role context, `GET /activities?team=mine`, migration 0013 (`command_log`).
* Phase 15: personal time tracker. Local encrypted store, timer, DST-safe recurrence, quick-add parser (68-line golden set), Google Calendar and Microsoft Graph/Teams adapters,
  ready-to-send tray with deterministic suggestions and learned rules, one-tap send with `calendar_ref` dedup, Unsend (`time.unlog`), end-of-day and weekly reviews, `tt day|week|month|add|timer|cal|tray|recur|...`,
  desktop/mobile React views (axe 100 in light and dark, 390 px), gateway `/v1/translate/personal`, `development_hours_below` policy and `learning_consistency` habit, ContextPack `development_project`/`calendar_mapping_hints`/`my_capacity_hours_per_week`.
  Schema minor 0.2 (`source`, `corrects`), migration 0014, ADR-0053..0055.
* Phases 13-15 follow-ups (PR #11 review): `pending_approvals` as a list, per-op token scopes, command rate limit and 50-command batch cap, `command.applied` event and a pinned commit allow-list, DailyLog drafts on time entry, glossary learning restored, `privacy.assist` for personal quick-add, neutral auto-sent meetings, `ReviewRow`/`TeamNowRow` mappings in `tt_client`, `tt-sidecar`, shared Gantt module, lazy route groups with an enforced bundle budget, mobile (390 px) axe pass, `filelock` override.
* Phase 15 leftovers (branch `leftovers/p15`): Microsoft delta-link expiry (410) triggers a full resync and deleted meetings are never resurrected; per-calendar exclude/include, never-offer lists and calendar-list refresh (`tt cal list|exclude|include|suppress|refresh`, desktop Calendars dialog);
  desktop shell tray, OS notifications through the `NotificationPort` (ready to send, end of day), palette hotkey and background calendar sync (compiles, `cargo test`; ADR-0058); the PyInstaller sidecar builds (125 MiB);
  "Mobile Lite", an installable offline-capable web app for the planner views (ADR-0057); `tools/smoke_calendar.py` live-account smoke test (not yet run live).
### Changed
* Light-theme text colours darkened to meet WCAG AA contrast.
### Fixed
* Contract fuzzing found and fixed 500s on negative/huge `limit`, NUL bytes, invalid enum filters, dangling references and an out-of-range `upcoming` filter.
* Certificate reload validates on a scratch SSL context (a failed load into the live context would have paired a new certificate with the old key).
### Known gaps
See `docs/uat/known-limitations.md` and the final review report.
