# The gateway is deployed on its own (team inference box, cloud, or laptop sidecar). Same workspace, own image.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
RUN uv sync --frozen --all-packages --no-dev
EXPOSE 8700
CMD ["tt-llm", "serve", "--host", "0.0.0.0", "--port", "8700", "--config", "/etc/tt/llm_gateway.yaml"]
