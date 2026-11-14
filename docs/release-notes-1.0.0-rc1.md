# Release notes - 1.0.0-rc1

Every architecture decision record in this release:

* ADR-0001 - ADR-0001 — Transactional projection instead of replay event sourcing
* ADR-0002 - Fuzzy match thresholds and tie policy
* ADR-0003 - Deterministic parser wins over the LLM for numbers and dates
* ADR-0004 - Optional `mode` on ClientBatch
* ADR-0005 - Stage -> state category defaults and the sync rules
* ADR-0006 - Review-queue ownership tie rule
* ADR-0007 - Weight formula grammar
* ADR-0008 - 404 for invisible objects, 403 for forbidden ones
* ADR-0009 - Server-sent events for review-queue updates
* ADR-0010 - Relative date tokens and time-zone resolution
* ADR-0011 - The prototype's section renderers are the spec; changes are additive
* ADR-0012 - Habit naming and polarity
* ADR-0013 - Dry-run of `on_event` policies is an approximation
* ADR-0014 - Coaching notes are manager-only
* ADR-0015 - Jira is a projection; inbound is limited to status, assignee and comments
* ADR-0016 - Propagation runs synchronously in the triggering transaction
* ADR-0017 - Permission checks are router dependencies; services reachable from several routes check themselves
* ADR-0018 - One LLM gateway on LiteLLM; clients and the core server are LLM-agnostic
* ADR-0019 - Write tools are per-task opt-in; the agent never bypasses the capture API
* ADR-0020 - pgvector not adopted yet
* ADR-0021 - Presentation entity and the generic slide template
* ADR-0022 - Active Directory via LDAPS/OIDC with group -> role/team mapping; HTTPS from PEM everywhere
* ADR-0023 - A workspace `manager` sees `members`-tier items without being a member
* ADR-0024 - The Inbox is invisible to admins
* ADR-0025 - API tokens are hashed with SHA-256, not a password hash
* ADR-0026 - Capture-side overlay hints are recorded as `source=client`
* ADR-0027 - `state_id` is tagged overlay but capture moves it
* ADR-0028 - The gateway uses `litellm.acompletion` with its own routing, not the LiteLLM `Router`
* ADR-0029 - Agent runs are kept in memory unless a storage URL is configured
* ADR-0030 - The filter compiler lives in the server package; `under` is a recursive CTE; `touched` is `EXISTS events`
* ADR-0031 - CEL conditions are type-checked by our own checker and bounded by static and runtime limits
* ADR-0032 - The snapshot range is `days` + `as_of`, cached per viewer
* ADR-0033 - Presentation events use entity type `saved_view`
* ADR-0034 - Exports run in the request; browser exports are concurrency-bounded
* ADR-0035 - Nested Active Directory groups are expanded client-side
* ADR-0036 - Kerberos SSO, Postgres `verify-full` and certificate hot-reload are scheduled work, not waived
* ADR-0037 - Presentation templates are data; the built-ins ship as JSON files
* ADR-0038 - The desktop and mobile shells talk to `tt_client` through a Python sidecar
* ADR-0039 - The JSON outbox file is the interchange format between shells
* ADR-0040 - `verify-projection` replaces `rebuild-projection`

## Upgrade notes
* Run `tt-admin migrate` (adds `login_attempts`, `presentation_templates`, review-fix indexes; all additive).
* New required production settings are validated at startup: `TT_SESSION_SECRET` (>= 32 bytes), `TT_DEV_AUTH` off, `TT_TRUSTED_PROXIES` when `TT_TRUSTED_PRINCIPAL_HEADER` is set.
* Light-theme colours changed for contrast; custom CSS that relied on the old tokens should be re-checked.
* `/metrics` is disabled outside dev unless `TT_METRICS_TOKEN` is set.

## Known gaps
See `docs/guide/15-known-limitations.md`.
