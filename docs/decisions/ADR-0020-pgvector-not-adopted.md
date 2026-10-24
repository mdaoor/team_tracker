# ADR-0020: pgvector not adopted yet
Date: 2026-10-24   Status: accepted (revisit in Phase 6)

## Decision
`POST /v1/embed` exists so a second matching signal can be added, but embeddings are not stored: fuzzy matching stays on
`pg_trgm` (ADR-0002), whose scores `GET /items/search` now exposes to the agent. Adding pgvector means an extension in the
Postgres image, a migration and a backfill job; none of the Phase 2b acceptance criteria need it.

## Consequences
Matching quality on paraphrases ("the oauth thing" vs "token exchange") relies on the model's choice among trigram candidates.
