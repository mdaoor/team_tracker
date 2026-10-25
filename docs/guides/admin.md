# Admin guide

1. **First run:** `make migrate`, create the workspace and owner (`POST /api/v1/workspaces` in dev, or the setup wizard), configure sign-in (OIDC, LDAPS/AD, Kerberos proxy) - `docs/security/`.
2. **Setup screens:** projects, people and teams, reporting lines, roles (workspace admin, manager, project owner, contributor, viewer; feature admins), dimensions and processes, milestones/iterations, risks, tags and glossary, translator rules, manual-mode requirements and context pack.
3. **Integrations:** Jira (`docs/integrations/jira.md`) with a stored, encrypted credential (`TT_SECRET_KEY`); LLM gateway (`docs/llm/gateway.md`), privacy mode `local_only|hybrid|cloud`, budgets.
4. **Workspace settings (JSON on the workspace):** `external_share`, `external_share_redaction` (`standard`|`strict`), `external_packs` (allow downloading generation packs, default off), `external_pack_redaction`,
   `raw_input_retention_days`, `plan_slip_days`, `auth.allowed_domains`, `daily_log_time`, `hours_per_day`.
5. **Privacy duties:** `GET /me/export`, `GET /people/{h}/export`, `POST /people/{h}:erase`, retention purge (`tt-admin purge-raw-input`, nightly job).
6. **Operations:** `docs/ops/config-reference.md`, `docs/ops/helm.md`, `deploy/systemd/`, `docs/runbooks/`, metrics + alert rules in `ops/`, backups and the restore drill in `docs/runbooks/restore.md`.
