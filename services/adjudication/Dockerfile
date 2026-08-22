FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# The image mirrors the repository layout rather than flattening it: this service's
# pyproject resolves pramana-common at ../../packages/common, and a flattened WORKDIR
# would make that path escape the filesystem root.
WORKDIR /app/services/adjudication
ENV PATH="/app/services/adjudication/.venv/bin:$PATH"

COPY packages/common /app/packages/common
COPY services/adjudication/pyproject.toml services/adjudication/uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project
COPY services/adjudication .
RUN uv sync --no-dev --frozen

RUN useradd --system --uid 10001 --create-home --home-dir /home/pramana pramana \
    && chown -R pramana /app /home/pramana
USER pramana

CMD ["uvicorn", "adjudication.main:app", "--host", "0.0.0.0", "--port", "8002"]
