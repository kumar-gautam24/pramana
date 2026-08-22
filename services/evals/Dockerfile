FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# packages/common first, and its own layer: it changes far less often than a service's
# own source, so a source edit does not invalidate its install.
COPY packages/common /app/packages/common
COPY services/evals/pyproject.toml services/evals/uv.lock /app/services/evals/
WORKDIR /app/services/evals
RUN uv sync --frozen --no-dev

COPY services/evals/src /app/services/evals/src
COPY services/evals/migrations /app/services/evals/migrations

EXPOSE 8003
CMD ["uv", "run", "--no-dev", "uvicorn", "evals.main:app", "--host", "0.0.0.0", "--port", "8003"]
