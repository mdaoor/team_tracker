# Team Tracker

Two-layer work tracking: reporters type one line (or fill a form), a local/independent LLM gateway structures it, managers enrich it server-side, and everything is rendered as views, decks and a CEL-governed policy engine.

- **Design spec (normative):** `docs/spec/` — object model v0.4, UX design v0.1, Python reference models, presentation-page prototype.
- **Implementation plans (handover to an implementing agent):** `plans/README.md` — start there. 14 phases + 5 appendices.
- **Tools:** `tools/gen_snapshot.py` — sample snapshot generator used by the prototype and as the test oracle.

Status: Phase 0 (bootstrap) implemented; Phase 1 (`plans/02-phase-1-core-domain-and-capture.md`) is next.

## Quick start
```
cp .env.example .env
make dev        # docker compose: postgres, api (:8000), worker, web (:5173)
make migrate    # alembic + procrastinate schema          (local Postgres, no Docker)
make seed       # demo workspace "acme" (tt-admin seed demo --reset to recreate)
make check      # lint + typecheck + tests
```
Tests need Postgres 16 with `pg_trgm`, `ltree`, `btree_gist`: they start a testcontainer, or use an existing empty
database when `TT_TEST_DATABASE_URL` is set.
