FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# packages/common first, and its own layer: it changes far less often than a service's
# own source, so a source edit does not invalidate its install.
COPY packages/common /app/packages/common
COPY services/auth/pyproject.toml services/auth/uv.lock /app/services/auth/
WORKDIR /app/services/auth
RUN uv sync --frozen --no-dev

COPY services/auth/src /app/services/auth/src
COPY services/auth/migrations /app/services/auth/migrations

EXPOSE 8004
CMD ["uv", "run", "--no-dev", "uvicorn", "auth.main:app", "--host", "0.0.0.0", "--port", "8004"]
