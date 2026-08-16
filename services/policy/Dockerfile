FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# The image mirrors the repository layout rather than flattening it: this service's
# pyproject resolves pramana-common at ../../packages/common, and a flattened WORKDIR
# would make that path escape the filesystem root.
WORKDIR /app/services/policy
ENV PATH="/app/services/policy/.venv/bin:$PATH"

COPY packages/common /app/packages/common
COPY services/policy/pyproject.toml services/policy/uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project
COPY services/policy .
RUN uv sync --no-dev --frozen

# HF_HOME is set explicitly and created before the user switch. useradd without a home
# leaves huggingface_hub writing to a path this user cannot create, and the model download
# then dies with EACCES on the first search -- a failure no unit test sees, because tests
# never run inside the container.
ENV HF_HOME=/app/.cache/huggingface
RUN useradd --system --uid 10001 --create-home --home-dir /home/pramana pramana \
    && mkdir -p /app/.cache/huggingface \
    && chown -R pramana /app /home/pramana
USER pramana

CMD ["uvicorn", "policy.main:app", "--host", "0.0.0.0", "--port", "8001"]
