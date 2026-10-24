.PHONY: dev down test lint typecheck check migrate seed openapi e2e fmt

PY ?= uv run

dev:            ## postgres + api + worker + web (docker compose)
	docker compose up --build

down:
	docker compose down

migrate:        ## alembic upgrade head + procrastinate schema
	$(PY) tt-admin migrate

seed:           ## demo workspace "acme" (use SEED_ARGS=--reset to recreate)
	$(PY) tt-admin seed demo $(SEED_ARGS)

test:           ## pytest (uses TT_TEST_DATABASE_URL, else testcontainers) + vitest
	$(PY) pytest -q --cov=tt_schema --cov=tt_server --cov=tt_client --cov=tt_cli --cov=tt_llm --cov-report=term-missing:skip-covered
	pnpm --filter @tt/web test

lint:
	$(PY) ruff check .
	$(PY) ruff format --check .
	pnpm --filter @tt/web lint

fmt:
	$(PY) ruff check --fix .
	$(PY) ruff format .
	pnpm --filter @tt/web format

typecheck:
	$(PY) mypy packages/schema/src packages/client_core/src apps/cli/tt_cli apps/server/tt_server apps/llm_gateway/tt_llm packages/filter_grammar/src packages/cel_policies/src
	pnpm --filter @tt/web typecheck

openapi:        ## docs/api/openapi.json + generated web client types
	$(PY) python -m tt_server.openapi docs/api/openapi.json
	pnpm --filter @tt/web gen:api

e2e:
	pnpm --filter @tt/web e2e
	pnpm --filter @tt/presentation e2e

presentation:   ## single-file deck bundle: apps/presentation/dist/presentation.html
	pnpm --filter @tt/presentation build

check: lint typecheck test
