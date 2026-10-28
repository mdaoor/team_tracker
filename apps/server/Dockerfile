# syntax=docker/dockerfile:1
# Phase 13 R3.6: multi-stage, non-root, no build tools in the runtime image.
# uv is pinned to a release AND its multi-arch index digest (resolved from the ghcr.io registry API) so the tag cannot move.
FROM ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
COPY tools ./tools
RUN uv sync --frozen --all-packages --no-dev

FROM python:3.12-slim AS runtime
RUN groupadd --gid 10001 tt && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin tt
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app
COPY docs ./docs
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8000/healthz')"]
CMD ["uvicorn", "tt_server.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
