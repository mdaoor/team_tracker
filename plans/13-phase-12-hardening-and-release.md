# Phase 12 — Hardening and release

## Goal
Production readiness: performance at scale, observability, backups and restore drills, security review, deployment artefacts, operational and user documentation, and the projection‑rebuild tool that makes the Event log truly the source of truth.

## Depends on
All previous phases (can start load/observability work after P6).

## Scope
1. **Performance**: load fixture (`tools/seed/load.py`: 200 members, 50 projects, 100 k items, 1 M events, 500 k classifications); p95 budgets per endpoint (capture batch < 150 ms/message, query items < 300 ms, snapshot < 2 s cold); index review with `pg_stat_statements`; partition `events` by month; `work_item_rollups` and `metric_snapshots` materialisation tuning; connection pooling (pgbouncer transaction mode compatible — no session state).
2. **Observability**: OTel traces across API → jobs (propagate `correlation_id` as a span attribute), RED metrics per route and per job, business metrics (batches/min, review queue size, violations open, LLM latency/cost), structured logs with request/actor ids, Grafana dashboards JSON in `ops/`, alert rules (job backlog, failed pushes, p95 breaches).
3. **Reliability**: Procrastinate retry policies and dead‑letter table, poison‑message handling, idempotency audits (chaos test: kill the worker mid‑job → no duplicate events), graceful shutdown, health/readiness tuned for k8s.
4. **Data**: backups (pgBackRest or managed snapshots), PITR, quarterly restore drill script, `tt-admin rebuild-projection <entity>` (replays `events` for an entity type into fresh projection tables — feasible because every mutation has an event; document which tables are rebuildable and verify on the seed: rebuild == current), data retention (`raw_input` purge after N days configurable; wellbeing habit retention), GDPR export/delete per person.
5. **Security**: dependency scanning (uv/pnpm audit in CI), SAST (bandit/semgrep), secrets in env/secret manager only, rate limiting on capture (per token) and auth endpoints, CSP for web and presentation page, CEL cost limits verified, threat model doc (STRIDE over the surfaces: capture API, webhooks, LLM prompts — prompt‑injection in reporter text must not change manager rules; the translator only outputs schema‑constrained JSON and rule 5 forbids inventing entities), pen‑test checklist, S3 storage adapter with signed URLs.
6. **Deployment**: Dockerfiles (multi‑stage, non‑root), Helm chart (`server`, `worker`, `web` static, `presentation`, `llm-gateway`), Compose for single‑node **Linux with HTTPS from PEM files** (cert/key/CA mounted at `/etc/tt/tls`, Caddy or Uvicorn TLS, auto‑reload on rotation, expiry alert), systemd unit files for non‑container installs, Active Directory connectivity checklist (LDAPS 636 reachable, CA PEM installed, service account, keytab for Kerberos), env matrix, migrations as a pre‑deploy job, blue/green notes, config reference generated from `Settings`.
7. **Docs**: admin guide (workspace setup, roles, Jira, LLM provider), reporter guide (modes, CLI, desktop, Slack), manager guide (console, policies), API reference (OpenAPI + event catalogue + snapshot schema + filter grammar), runbooks (job backlog, failed Jira pushes, LLM outage, restore), CHANGELOG, versioning policy (`context_schema_version`, API `v1`).
8. **Release**: `v1.0.0` tag, release notes, upgrade notes, demo workspace script, acceptance walkthrough of the three UX flows (§7.1–7.3) recorded as a Playwright video.

## Acceptance criteria
- [ ] Load test report committed with p95 numbers meeting budgets; any miss has an issue and a mitigation.
- [ ] `tt-admin rebuild-projection work_item` on the seed produces tables equal to the originals (row‑wise diff empty).
- [ ] Chaos test passes (worker kill → no duplicates, no loss).
- [ ] Restore drill completes from backup to a working stack in < 30 min following the runbook.
- [ ] Security scan has no high/critical findings; threat model reviewed; CSP enforced with zero violations in Playwright.
- [ ] Helm install on a kind cluster passes smoke tests in CI.
- [ ] Docs build (MkDocs) with no broken links; every endpoint documented.

## Exit checklist
- [ ] `v1.0.0` tagged; release notes list every ADR.
- [ ] Backlog of deferred items (TS port of `tt_client`, plugin mechanism for Tier‑3 functions §14.1, Teams bot, S3 multi‑region) filed as issues.
