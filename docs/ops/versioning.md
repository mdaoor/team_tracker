# Versioning policy

* **Product:** SemVer. `1.x` keeps the HTTP API `/api/v1`, the `ClientBatch`/`ContextPack` formats and the snapshot/presentation schemas backward compatible; additive changes only.
* **API:** `/api/v1` is frozen for additive evolution; a breaking change means `/api/v2` alongside `v1` for at least one minor release. `docs/api/openapi.json` is the contract and is diff-checked in CI.
* **`context_schema_version` (CEL policy context):** bumped when a root/field is removed or changes meaning; old policies keep evaluating against their recorded version until edited (`docs/governance/context-schema-v1.md`).
* **Snapshot / presentation / template schemas:** carry `schema_version`; the server reads N and N-1. Generation packs carry `pack_version`.
* **Database:** Alembic migrations are forward-only and additive within a minor release (new nullable columns/tables first, destructive cleanup a release later) so blue/green and rollback by redeploying the previous image work.
* **Events:** event types are an allow-list (`docs/api/event-catalogue.md`); payload fields are only added, never repurposed.
* **Deprecation:** announce in CHANGELOG, log a warning for one minor release, remove in the next.
