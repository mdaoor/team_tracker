FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
COPY docs ./docs
COPY tools ./tools
RUN uv sync --frozen --all-packages --no-dev
