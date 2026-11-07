# Release notes - 1.0.0-rc1

Every architecture decision record in this release:

* [ADR-0001](decisions/ADR-0001-transactional-projection.md) - ADR-0001 — Transactional projection instead of replay event sourcing
* [ADR-0002](decisions/ADR-0002-fuzzy-match-thresholds.md) - Fuzzy match thresholds and tie policy
* [ADR-0003](decisions/ADR-0003-deterministic-parser-wins.md) - Deterministic parser wins over the LLM for numbers and dates
* [ADR-0004](decisions/ADR-0004-batch-mode-field.md) - Optional `mode` on ClientBatch
* [ADR-0005](decisions/ADR-0005-stage-state-defaults.md) - Stage -> state category defaults and the sync rules
* [ADR-0006](decisions/ADR-0006-review-queue-ownership.md) - Review-queue ownership tie rule
* [ADR-0007](decisions/ADR-0007-weight-formula-grammar.md) - Weight formula grammar
* [ADR-0008](decisions/ADR-0008-404-for-invisible-objects.md) - 404 for invisible objects, 403 for forbidden ones
* [ADR-0009](decisions/ADR-0009-sse-review-queue.md) - Server-sent events for review-queue updates
* [ADR-0010](decisions/ADR-0010-relative-date-tokens.md) - Relative date tokens and time-zone resolution
* [ADR-0011](decisions/ADR-0011-prototype-renderers-are-the-spec.md) - The prototype's section renderers are the spec; changes are additive
* [ADR-0012](decisions/ADR-0012-habit-naming-and-polarity.md) - Habit naming and polarity
* [ADR-0013](decisions/ADR-0013-on-event-dry-run-approximation.md) - Dry-run of `on_event` policies is an approximation
* [ADR-0014](decisions/ADR-0014-coaching-note-visibility.md) - Coaching notes are manager-only
* [ADR-0015](decisions/ADR-0015-jira-is-a-projection.md) - Jira is a projection; inbound is limited to status, assignee and comments
* [ADR-0016](decisions/ADR-0016-propagation-is-synchronous.md) - Propagation runs synchronously in the triggering transaction
* [ADR-0017](decisions/ADR-0017-permission-checks-and-self-checking-services.md) - Permission checks are router dependencies; services reachable from several routes check themselves
* [ADR-0018](decisions/ADR-0018-single-llm-gateway.md) - One LLM gateway on LiteLLM; clients and the core server are LLM-agnostic
* [ADR-0019](decisions/ADR-0019-write-tools-opt-in.md) - Write tools are per-task opt-in; the agent never bypasses the capture API
* [ADR-0020](decisions/ADR-0020-pgvector-not-adopted.md) - pgvector not adopted yet
* [ADR-0021](decisions/ADR-0021-presentation-entity-and-slide-template.md) - Presentation entity and the generic slide template
* [ADR-0022](decisions/ADR-0022-active-directory-and-pem-tls.md) - Active Directory via LDAPS/OIDC with group -> role/team mapping; HTTPS from PEM everywhere
* [ADR-0023](decisions/ADR-0023-manager-sees-members-tier.md) - A workspace `manager` sees `members`-tier items without being a member
* [ADR-0024](decisions/ADR-0024-inbox-is-private-even-from-admins.md) - The Inbox is invisible to admins
* [ADR-0025](decisions/ADR-0025-token-hashing-sha256.md) - API tokens are hashed with SHA-256, not a password hash
* [ADR-0026](decisions/ADR-0026-capture-hints-are-source-client.md) - Capture-side overlay hints are recorded as `source=client`
* [ADR-0027](decisions/ADR-0027-capture-moves-state-id.md) - `state_id` is tagged overlay but capture moves it
* [ADR-0028](decisions/ADR-0028-litellm-router-not-used.md) - The gateway uses `litellm.acompletion` with its own routing, not the LiteLLM `Router`
* [ADR-0029](decisions/ADR-0029-agent-runs-in-memory-by-default.md) - Agent runs are kept in memory unless a storage URL is configured
* [ADR-0030](decisions/ADR-0030-filter-compiler-in-server.md) - The filter compiler lives in the server package; `under` is a recursive CTE; `touched` is `EXISTS events`
* [ADR-0031](decisions/ADR-0031-static-cel-checker-and-limits.md) - CEL conditions are type-checked by our own checker and bounded by static and runtime limits
* [ADR-0032](decisions/ADR-0032-snapshot-range-days-and-as-of.md) - The snapshot range is `days` + `as_of`, cached per viewer
* [ADR-0033](decisions/ADR-0033-presentation-events-on-saved-view.md) - Presentation events use entity type `saved_view`
* [ADR-0034](decisions/ADR-0034-exports-are-synchronous-and-bounded.md) - Exports run in the request; browser exports are concurrency-bounded
* [ADR-0035](decisions/ADR-0035-nested-ad-groups-client-side.md) - Nested Active Directory groups are expanded client-side
* [ADR-0036](decisions/ADR-0036-deferred-sso-and-database-tls.md) - Kerberos SSO, Postgres `verify-full` and certificate hot-reload are scheduled work, not waived
* [ADR-0037](decisions/ADR-0037-templates-are-data.md) - Presentation templates are data; the built-ins ship as JSON files
* [ADR-0038](decisions/ADR-0038-desktop-tt-client-sidecar.md) - The desktop and mobile shells talk to `tt_client` through a Python sidecar
* [ADR-0039](decisions/ADR-0039-outbox-json-is-the-interchange-format.md) - The JSON outbox file is the interchange format between shells
* [ADR-0040](decisions/ADR-0040-verify-projection-instead-of-replay.md) - `verify-projection` replaces `rebuild-projection`

## Upgrade notes
* Run `tt-admin migrate` (adds `login_attempts`, `presentation_templates`, review-fix indexes; all additive).
* New required production settings are validated at startup: `TT_SESSION_SECRET` (>= 32 bytes), `TT_DEV_AUTH` off, `TT_TRUSTED_PROXIES` when `TT_TRUSTED_PRINCIPAL_HEADER` is set.
* Light-theme colours changed for contrast; custom CSS that relied on the old tokens should be re-checked.
* `/metrics` is disabled outside dev unless `TT_METRICS_TOKEN` is set.

## Known gaps
See `docs/uat/known-limitations.md`.
