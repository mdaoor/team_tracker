# syntax=docker/dockerfile:1
# The gateway is deployed on its own (team inference box, cloud, or laptop sidecar). Same workspace, own image.
# Phase 13 R3.6: multi-stage, non-root. See apps/server/Dockerfile for the uv pin note.
FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
RUN uv sync --frozen --all-packages --no-dev

FROM python:3.12-slim AS runtime
RUN groupadd --gid 10001 tt && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin tt
WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app
USER 10001:10001
EXPOSE 8700
CMD ["tt-llm", "serve", "--host", "0.0.0.0", "--port", "8700", "--config", "/etc/tt/llm_gateway.yaml"]
