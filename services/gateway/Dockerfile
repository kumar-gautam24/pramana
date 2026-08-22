FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# packages/common first, and its own layer: it changes far less often than a service's
# own source, so a source edit does not invalidate its install.
COPY packages/common /app/packages/common
COPY services/gateway/pyproject.toml services/gateway/uv.lock /app/services/gateway/
WORKDIR /app/services/gateway
RUN uv sync --frozen --no-dev

COPY services/gateway/src /app/services/gateway/src

# No migrations: the gateway owns no database, by design.
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
