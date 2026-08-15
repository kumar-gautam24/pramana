# Pramana Plan 02 — Policy Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `policy` service — it ingests CMS National Coverage Determinations, versions them by effective date, chunks them along their heading structure, and answers hybrid retrieval queries over them.

**Architecture:** A FastAPI service owning `pramana_policy`. Ingest fetches from the CMS Coverage API, parses the double-escaped HTML into a heading tree, chunks along headings so every chunk keeps its heading path, embeds locally, and stores. Retrieval fuses dense (pgvector) and lexical (Postgres full-text) rankings with Reciprocal Rank Fusion, then reranks with a cross-encoder. Parsing and chunking are pure functions with no I/O so they can be tested exhaustively against a recorded fixture.

**Tech Stack:** Python 3.12, uv, FastAPI, SQLAlchemy async, Alembic, Postgres 16 + pgvector, fastembed (BAAI/bge-small-en-v1.5), cross-encoder `ms-marco-MiniLM-L-6-v2`, httpx, pytest, ruff.

## Global Constraints

- Python 3.12. Dependencies with `uv`; lockfile committed.
- The service owns `pramana_policy` and no other database. No cross-service joins.
- `packages/common` is the only shared import. `policy` must not import from another service.
- **No CPT code descriptions in the repository.** The Coverage API payload for NCDs carries none; if a future endpoint returns any, strip it before persisting. ICD-10 and HCPCS are unrestricted. See `docs/decisions/0004-cms-corpus-and-cpt-licensing.md`.
- **No hardcoded per-policy logic.** Nothing may branch on `240.4` or any other document id. Policy is data. See `docs/decisions/0003`.
- Misconfiguration fails at startup, not on first request.
- Comments explain **why**, never what.
- Commits: conventional style, imperative, lowercase subject. **Never any attribution trailer** — no `Co-Authored-By`, no generated-with footer, no model or tool name.
- Tests required on critical paths: HTML parsing, chunking, effective-date resolution, RRF fusion, and ingest idempotency. Route wiring and CRUD need not be covered.
- The local stack runs on overridden ports (`DB_PORT=5433 REDIS_PORT=6380`) because another project holds 5432/6379 on this machine. Use those overrides in every verification command.

## Verified facts about the upstream API

These were confirmed against the live API on 2026-08-16. Do not re-derive them; do not assume anything beyond them.

- `GET https://api.coverage.cms.gov/v1/data/ncd?ncdid=226` → HTTP 200, ~17 KB JSON. **No API key and no license token required.**
- Response shape: `{"meta": {"status": {"id": 200, "message": "OK"}, "fields": [...]}, "data": [ {...} ]}` — `data` is a list with one record per version returned.
- Record fields used by this service: `document_id` (str, e.g. `"226"`), `document_version` (int, e.g. `3`), `document_display_id` (str, e.g. `"240.4"`), `title`, `effective_date` (str, `MM/DD/YYYY`), `effective_end_date` (str, `MM/DD/YYYY` **or the literal `"N/A"`**), `benefit_category`, `item_service_description` (HTML), `indications_limitations` (HTML, the criteria text), `cross_reference`, `revision_history`, `reasons_for_denial`, `other_text`, `ama_statement`.
- **The HTML is double-escaped.** `html.unescape` must be applied **twice** before parsing: the raw value contains `&lt;p&gt;` and `&sol;`, which unescape to `<p>` and `/` only on the second pass.
- **Headings are `<strong>` tags, not `<h1>`–`<h6>`.** In NCD 240.4 they are `B.   Nationally Covered Indications`, `C.    Nationally Non-covered Indications`, `D.    Other` — a letter, a period, runs of non-breaking space, then the title. A generic markdown or `<h*>` chunker finds zero headings in this corpus.
- `240.4` has `effective_date = "03/13/2008"` and `effective_end_date = "N/A"`, meaning open-ended.
- Sleep testing is a separate document: NCD **240.4.1**, `ncdid=330`.

## File Structure

| file | responsibility |
| --- | --- |
| `services/policy/pyproject.toml` | package metadata, deps, ruff + pytest config |
| `services/policy/src/policy/config.py` | settings; fails at import if misconfigured |
| `services/policy/src/policy/main.py` | FastAPI app, routes, lifespan |
| `services/policy/src/policy/db.py` | async engine and session factory |
| `services/policy/src/policy/models.py` | `Policy`, `Chunk` ORM models |
| `services/policy/src/policy/cms.py` | Coverage API client — the only module that talks to CMS |
| `services/policy/src/policy/parsing.py` | **pure**: double-unescape, HTML → `Section` list with heading paths |
| `services/policy/src/policy/chunking.py` | **pure**: `Section` list → `Chunk` records |
| `services/policy/src/policy/dating.py` | **pure**: choose the version in force on a date |
| `services/policy/src/policy/embedding.py` | fastembed wrapper; loads at startup, not per request |
| `services/policy/src/policy/retrieval.py` | dense + lexical + RRF + rerank |
| `services/policy/src/policy/ingest.py` | orchestrates fetch → parse → chunk → embed → store |
| `services/policy/migrations/` | Alembic; one migration for `policies` + `chunks` |
| `services/policy/tests/fixtures/ncd-226.json` | recorded live response; the corpus for every parsing test |
| `services/policy/Dockerfile` | image, non-root user, HF cache path |

---

### Task 1: Service skeleton

**Files:**
- Create: `services/policy/pyproject.toml`, `src/policy/__init__.py`, `src/policy/config.py`, `src/policy/main.py`, `tests/test_health.py`, `Dockerfile`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: `pramana-common` from `packages/common` (path dependency)
- Produces: a FastAPI app exposing `GET /health` → `{"status": "ok"}` and `GET /ready`; `get_settings()` returning a `Settings` object with `database_url: str`, `embedding_model: str`, `rerank_model: str`, `cms_base_url: str`.

- [ ] **Step 1: Write the failing test**

Create `services/policy/tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from policy.main import app


def test_health_reports_ok():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/policy && uv run pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'policy'`.

- [ ] **Step 3: Create the package**

Create `services/policy/pyproject.toml`:

```toml
[project]
name = "policy"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pramana-common",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "pgvector>=0.3",
    "httpx>=0.27",
    "pydantic-settings>=2.4",
    "fastembed>=0.4",
    "lxml>=5.3",
]

[tool.uv.sources]
pramana-common = { path = "../../packages/common", editable = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/policy"]

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.6", "httpx>=0.27"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

Create `services/policy/src/policy/__init__.py` as an empty file.

Create `services/policy/src/policy/config.py`:

```python
"""Settings, resolved once at import.

A bad configuration must stop the service from starting rather than surface as a failed
request an hour later, so there are no defaults for values that have no safe default."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    cms_base_url: str = "https://api.coverage.cms.gov/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Create `services/policy/src/policy/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="pramana policy")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd services/policy && DATABASE_URL=postgresql+asyncpg://pramana:pramana@localhost:5433/pramana_policy uv sync && DATABASE_URL=postgresql+asyncpg://pramana:pramana@localhost:5433/pramana_policy uv run pytest -v`
Expected: PASS, 1 test.

- [ ] **Step 5: Add the Dockerfile**

Create `services/policy/Dockerfile`:

```dockerfile
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
```

- [ ] **Step 6: Wire into compose**

Add to `docker-compose.yml` under `services:`, after `redis`:

```yaml
  policy:
    build:
      context: .
      dockerfile: services/policy/Dockerfile
    environment:
      DATABASE_URL: postgresql+asyncpg://pramana:pramana@db:5432/pramana_policy
    ports: ["${POLICY_PORT:-8001}:8001"]
    # The model cache is a named volume so a rebuilt container does not re-download the
    # embedder and reranker, which otherwise blows past the first request's timeout.
    volumes:
      - hfcache:/app/.cache/huggingface
    depends_on:
      db:
        condition: service_healthy
```

Add `hfcache:` to the `volumes:` block at the bottom of the file.

- [ ] **Step 7: Verify the container starts and answers**

Run:

```bash
DB_PORT=5433 REDIS_PORT=6380 docker compose up -d --build policy
curl -s localhost:8001/health
```

Expected: `{"status":"ok"}`.

- [ ] **Step 8: Commit**

```bash
git add services/policy docker-compose.yml
git commit -m "add policy service skeleton"
```

---

### Task 2: Schema and migration

**Files:**
- Create: `services/policy/src/policy/db.py`, `src/policy/models.py`, `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_policies_and_chunks.py`
- Test: `services/policy/tests/test_models.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 1
- Produces:
  - `Policy` — `id`, `document_id: str`, `document_version: int`, `display_id: str`, `title: str`, `effective_from: date`, `effective_to: date | None`, `benefit_category: str`, `source_url: str`, `ingested_at: datetime`. Unique constraint on `(document_id, document_version)`.
  - `Chunk` — `id`, `policy_id: int` (FK), `ordinal: int`, `heading_path: str`, `text: str`, `embedding: Vector(384)`, `tsv` generated full-text column.
  - `SessionFactory` — async session factory.

- [ ] **Step 1: Write the failing test**

Create `services/policy/tests/test_models.py`:

```python
from policy.models import Chunk, Policy


def test_policy_is_unique_per_document_version():
    """Ingest must be idempotent: re-running it for a version already stored is a
    no-op rather than a duplicate corpus."""
    constraints = {c.name for c in Policy.__table__.constraints if c.name}

    assert "uq_policies_document_id_version" in constraints


def test_chunk_embedding_is_384_dimensions():
    """bge-small-en-v1.5 emits 384 dimensions. A mismatch here fails at insert time with
    an opaque pgvector error, so pin it in the model."""
    assert Chunk.__table__.c.embedding.type.dim == 384


def test_chunk_keeps_its_heading_path():
    """A citation that cannot name the section it came from is not auditable, so the
    column is not nullable."""
    assert Chunk.__table__.c.heading_path.nullable is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/policy && uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'policy.models'`.

- [ ] **Step 3: Write `db.py`**

```python
"""Async engine and session factory.

Created at import so a bad DATABASE_URL fails the service at startup rather than on the
first query."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from policy.config import get_settings

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

- [ ] **Step 4: Write `models.py`**

```python
"""The corpus: coverage determinations and the chunks retrieval searches over."""

from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(String(32), nullable=False)
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The number a human uses, e.g. "240.4". Distinct from document_id ("226"), which is
    #: the API's internal key -- both are needed, and confusing them silently retrieves
    #: the wrong policy.
    display_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    #: NULL means open-ended. The API expresses this as the literal string "N/A".
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    benefit_category: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id", "document_version", name="uq_policies_document_id_version"
        ),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_id: Mapped[int] = mapped_column(
        ForeignKey("policies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(384), nullable=False)
    #: Generated rather than populated in Python: the database is the only place that can
    #: guarantee it stays in step with `text`.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )

    __table_args__ = (Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/policy && uv run pytest tests/test_models.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 6: Create the migration**

Initialise Alembic (`uv run alembic init migrations`), point `migrations/env.py` at `policy.models.Base.metadata` and at `get_settings().database_url`, then autogenerate:

```bash
cd services/policy
DATABASE_URL=postgresql+asyncpg://pramana:pramana@localhost:5433/pramana_policy \
  uv run alembic revision --autogenerate -m "policies and chunks"
```

Open the generated file and **add `op.execute("CREATE EXTENSION IF NOT EXISTS vector")` as the first operation in `upgrade()`** — autogenerate does not emit it, and without it the `Vector` column fails on a fresh database.

- [ ] **Step 7: Apply and verify**

```bash
cd services/policy
DATABASE_URL=postgresql+asyncpg://pramana:pramana@localhost:5433/pramana_policy \
  uv run alembic upgrade head
docker compose exec -T db psql -U pramana -d pramana_policy -c "\d chunks"
```

Expected: `chunks` exists with an `embedding vector(384)` column and a `tsv` generated column.

- [ ] **Step 8: Commit**

```bash
git add services/policy
git commit -m "add policies and chunks schema"
```

---

### Task 3: CMS Coverage API client and recorded fixture

**Files:**
- Create: `services/policy/src/policy/cms.py`, `tests/fixtures/ncd-226.json`, `tests/test_cms.py`

**Interfaces:**
- Consumes: `get_settings()`
- Produces:
  - `NcdRecord` — frozen dataclass: `document_id: str`, `document_version: int`, `display_id: str`, `title: str`, `effective_from: date`, `effective_to: date | None`, `benefit_category: str`, `sections_html: dict[str, str]`, `source_url: str`
  - `parse_ncd_response(payload: dict) -> list[NcdRecord]` — **pure**, no I/O
  - `async fetch_ncd(client: httpx.AsyncClient, ncd_id: str) -> list[NcdRecord]`
  - `parse_cms_date(value: str) -> date | None` — `MM/DD/YYYY`, and `None` for `"N/A"` or empty

`sections_html` maps a section name to its raw HTML, taken from these payload fields: `item_service_description`, `indications_limitations`, `cross_reference`, `reasons_for_denial`, `other_text`. Empty values are omitted.

- [ ] **Step 1: Record the fixture from the live API**

```bash
mkdir -p services/policy/tests/fixtures
curl -s "https://api.coverage.cms.gov/v1/data/ncd?ncdid=226" \
  -o services/policy/tests/fixtures/ncd-226.json
python3 -c "import json;d=json.load(open('services/policy/tests/fixtures/ncd-226.json'));print(d['meta']['status'],len(d['data']))"
```

Expected: status OK, at least 1 record. This file is the corpus for every parsing test, so tests never depend on the network. Verify it contains no CPT code descriptions before committing (`grep -ci "current procedural terminology" services/policy/tests/fixtures/ncd-226.json` should be 0).

- [ ] **Step 2: Write the failing test**

Create `services/policy/tests/test_cms.py`:

```python
import json
from datetime import date
from pathlib import Path

import pytest

from policy.cms import parse_cms_date, parse_ncd_response

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ncd-226.json").read_text())


def test_parses_the_recorded_ncd():
    records = parse_ncd_response(FIXTURE)

    assert len(records) >= 1
    record = records[0]
    assert record.document_id == "226"
    assert record.display_id == "240.4"
    assert record.effective_from == date(2008, 3, 13)


def test_open_ended_policy_has_no_end_date():
    """The API writes an open-ended policy as the literal string "N/A". Storing that as a
    date would fail; storing it as a far-future date would silently expire the policy."""
    assert parse_cms_date("N/A") is None
    assert parse_cms_date("") is None


def test_parses_a_bounded_end_date():
    assert parse_cms_date("12/31/2019") == date(2019, 12, 31)


@pytest.mark.parametrize("bad", ["2008-03-13", "13/03/2008", "not a date"])
def test_unparseable_date_raises_rather_than_guessing(bad):
    """A misread effective date adjudicates a case against the wrong version of policy,
    which is worse than refusing to ingest."""
    with pytest.raises(ValueError):
        parse_cms_date(bad)


def test_criteria_section_is_captured():
    record = parse_ncd_response(FIXTURE)[0]

    assert "indications_limitations" in record.sections_html
    assert len(record.sections_html["indications_limitations"]) > 1000


def test_empty_sections_are_omitted():
    """NCD 226 has empty other_text and ama_statement. Carrying empty sections into
    chunking produces chunks with no content."""
    record = parse_ncd_response(FIXTURE)[0]

    assert all(value.strip() for value in record.sections_html.values())
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd services/policy && uv run pytest tests/test_cms.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'policy.cms'`.

- [ ] **Step 4: Write `cms.py`**

```python
"""The only module that talks to the CMS Coverage API.

Confirmed against the live API on 2026-08-16: GET /v1/data/ncd?ncdid=<id> returns
{"meta": {...}, "data": [ ... ]} and needs neither an API key nor a licence token. NCD
payloads carry no CPT descriptions, so nothing here has to strip them -- but see
docs/decisions/0004 before adding another endpoint."""

from dataclasses import dataclass
from datetime import date, datetime

import httpx

#: Payload fields carrying prose worth retrieving. Ordered as a reader meets them.
SECTION_FIELDS = (
    "item_service_description",
    "indications_limitations",
    "cross_reference",
    "reasons_for_denial",
    "other_text",
)

_VIEW_URL = "https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?ncdid={id}&ncdver={ver}"


@dataclass(frozen=True)
class NcdRecord:
    document_id: str
    document_version: int
    display_id: str
    title: str
    effective_from: date
    effective_to: date | None
    benefit_category: str
    sections_html: dict[str, str]
    source_url: str


def parse_cms_date(value: str) -> date | None:
    """MM/DD/YYYY, or None for an open-ended policy.

    The API writes "no end date" as the literal string "N/A". Anything else that will not
    parse raises: a misread effective date adjudicates a case against the wrong version of
    policy, which is a worse outcome than refusing to ingest it."""
    text = (value or "").strip()
    if not text or text.upper() == "N/A":
        return None
    return datetime.strptime(text, "%m/%d/%Y").date()


def parse_ncd_response(payload: dict) -> list[NcdRecord]:
    records = []
    for row in payload.get("data", []):
        effective_from = parse_cms_date(row["effective_date"])
        if effective_from is None:
            # A policy with no start date cannot be placed on a timeline, so it cannot be
            # matched to a date of service. Skipping is safer than assuming a date.
            continue
        records.append(
            NcdRecord(
                document_id=str(row["document_id"]),
                document_version=int(row["document_version"]),
                display_id=row["document_display_id"],
                title=row["title"],
                effective_from=effective_from,
                effective_to=parse_cms_date(row.get("effective_end_date", "")),
                benefit_category=row.get("benefit_category", ""),
                sections_html={
                    field: row[field]
                    for field in SECTION_FIELDS
                    if (row.get(field) or "").strip()
                },
                source_url=_VIEW_URL.format(
                    id=row["document_id"], ver=row["document_version"]
                ),
            )
        )
    return records


async def fetch_ncd(client: httpx.AsyncClient, ncd_id: str) -> list[NcdRecord]:
    response = await client.get("/data/ncd", params={"ncdid": ncd_id})
    response.raise_for_status()
    return parse_ncd_response(response.json())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/policy && uv run pytest tests/test_cms.py -v && uv run ruff check .`
Expected: PASS, 8 tests (parametrised cases expand), ruff clean.

- [ ] **Step 6: Commit**

```bash
git add services/policy
git commit -m "add cms coverage api client

Records a live NCD 240.4 response as a fixture so parsing tests never touch the
network."
```

---

### Task 4: HTML parsing into sections

**Files:**
- Create: `services/policy/src/policy/parsing.py`
- Test: `services/policy/tests/test_parsing.py`

**Interfaces:**
- Consumes: nothing (pure)
- Produces:
  - `Section` — frozen dataclass `(heading_path: str, text: str)`
  - `unescape_twice(raw: str) -> str`
  - `html_to_sections(raw_html: str, root_heading: str) -> list[Section]`

`heading_path` joins levels with `" > "`, e.g. `Indications and Limitations > B. Nationally Covered Indications`.

- [ ] **Step 1: Write the failing test**

Create `services/policy/tests/test_parsing.py`:

```python
import json
from pathlib import Path

from policy.cms import parse_ncd_response
from policy.parsing import html_to_sections, unescape_twice

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ncd-226.json").read_text())
CRITERIA_HTML = parse_ncd_response(FIXTURE)[0].sections_html["indications_limitations"]


def test_entities_need_two_passes():
    """The API double-escapes: the stored value contains "&amp;lt;p&amp;gt;", which only
    becomes "<p>" after unescaping twice. One pass leaves markup the parser cannot see."""
    assert unescape_twice("&amp;lt;p&amp;gt;") == "<p>"
    assert "<p>" in unescape_twice(CRITERIA_HTML)


def test_solidus_entity_becomes_a_slash():
    """CMS encodes "/" as "&sol;", so closing tags arrive as "&lt;&sol;p&gt;"."""
    assert unescape_twice("&amp;lt;&amp;sol;p&amp;gt;") == "</p>"


def test_finds_the_lettered_headings():
    """Headings in this corpus are <strong> tags carrying a letter prefix, not <h1>-<h6>.
    A generic heading parser finds none of them."""
    sections = html_to_sections(CRITERIA_HTML, root_heading="Indications and Limitations")
    headings = [s.heading_path for s in sections]

    assert any("Nationally Covered Indications" in h for h in headings)
    assert any("Nationally Non-covered Indications" in h for h in headings)


def test_heading_paths_are_rooted():
    sections = html_to_sections(CRITERIA_HTML, root_heading="Indications and Limitations")

    assert all(s.heading_path.startswith("Indications and Limitations") for s in sections)


def test_no_markup_survives_into_text():
    sections = html_to_sections(CRITERIA_HTML, root_heading="Indications and Limitations")

    assert not any("<" in s.text or "&lt;" in s.text for s in sections)


def test_the_ahi_criteria_survive_parsing():
    """The numeric criteria are what the whole system reasons over. If parsing drops or
    mangles them, every downstream stage is working from nothing."""
    sections = html_to_sections(CRITERIA_HTML, root_heading="Indications and Limitations")
    body = " ".join(s.text for s in sections)

    assert "greater than or equal to 15 events per hour" in body
    assert "12-week" in body


def test_nonbreaking_space_is_normalised():
    """CMS pads headings with runs of &#160;. Left in place, the heading path contains
    invisible characters and no two citations to the same section compare equal."""
    sections = html_to_sections(CRITERIA_HTML, root_heading="Indications and Limitations")

    assert not any("\xa0" in s.heading_path for s in sections)
    assert not any("  " in s.heading_path for s in sections)


def test_content_before_the_first_heading_is_kept():
    """Prose ahead of the first <strong> belongs to the root section. Dropping it loses
    the preamble that scopes everything after it."""
    html = "<p>Intro prose.</p><p><strong>A. First</strong></p><p>Body.</p>"
    sections = html_to_sections(html, root_heading="Root")

    assert sections[0].heading_path == "Root"
    assert "Intro prose." in sections[0].text


def test_empty_html_produces_no_sections():
    assert html_to_sections("", root_heading="Root") == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/policy && uv run pytest tests/test_parsing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'policy.parsing'`.

- [ ] **Step 3: Write `parsing.py`**

```python
"""HTML from the Coverage API into flat, heading-tagged sections.

Pure and I/O-free so it can be tested exhaustively against a recorded fixture rather than
against the network."""

import html
import re
from dataclasses import dataclass

from lxml import etree

#: A heading in this corpus looks like "B.   Nationally Covered Indications" -- a letter or
#: number, a period, whitespace, then the title. CMS marks these with <strong> rather than
#: a heading tag, so structure has to be recovered from the text pattern.
_HEADING = re.compile(r"^\(?([A-Z0-9]{1,3})[.)]\s+\S")


@dataclass(frozen=True)
class Section:
    heading_path: str
    text: str


def unescape_twice(raw: str) -> str:
    """The payload is escaped twice: "&amp;lt;p&amp;gt;" reaches "<p>" only on the second
    pass. Unescaping once leaves markup the parser cannot see, and it silently returns a
    single blob with no headings at all."""
    return html.unescape(html.unescape(raw or ""))


def _clean(text: str) -> str:
    # Non-breaking spaces pad every CMS heading. Left in, a heading path carries invisible
    # characters and two citations to the same section never compare equal.
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def html_to_sections(raw_html: str, root_heading: str) -> list[Section]:
    markup = unescape_twice(raw_html).strip()
    if not markup:
        return []

    root = etree.fromstring(f"<root>{markup}</root>", etree.HTMLParser(recover=True))
    if root is None:
        return []

    heading = root_heading
    buffer: list[str] = []
    sections: list[Section] = []

    def flush() -> None:
        body = _clean(" ".join(buffer))
        if body:
            sections.append(Section(heading_path=heading, text=body))
        buffer.clear()

    for element in root.iter():
        if element.tag == "strong":
            candidate = _clean("".join(element.itertext()))
            if _HEADING.match(candidate):
                flush()
                heading = f"{root_heading} > {candidate}"
                continue
        if element.text and element.tag not in ("script", "style"):
            buffer.append(element.text)
        if element.tail:
            buffer.append(element.tail)

    flush()
    return sections
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/policy && uv run pytest tests/test_parsing.py -v`
Expected: PASS, 9 tests.

If `test_the_ahi_criteria_survive_parsing` fails, the text is being dropped or split — fix the traversal rather than relaxing the assertion. That test is the one that proves the corpus is intact.

- [ ] **Step 5: Commit**

```bash
git add services/policy
git commit -m "parse coverage determination html into heading-tagged sections

CMS double-escapes its html and marks headings with <strong> rather than heading
tags, so both are handled explicitly and pinned by tests."
```

---

### Task 5: Chunking

**Files:**
- Create: `services/policy/src/policy/chunking.py`
- Test: `services/policy/tests/test_chunking.py`

**Interfaces:**
- Consumes: `Section` from Task 4
- Produces:
  - `ChunkRecord` — frozen dataclass `(ordinal: int, heading_path: str, text: str)`
  - `chunk_sections(sections: list[Section], max_chars: int = 1200, overlap_chars: int = 150) -> list[ChunkRecord]`

Sections shorter than `max_chars` become one chunk. Longer ones split on sentence boundaries with overlap. **Every chunk keeps its section's full heading path** — that is what makes a citation navigable.

- [ ] **Step 1: Write the failing test**

Create `services/policy/tests/test_chunking.py`:

```python
import pytest

from policy.chunking import chunk_sections
from policy.parsing import Section


def test_a_short_section_becomes_one_chunk():
    sections = [Section(heading_path="Root > A. First", text="Short body.")]

    chunks = chunk_sections(sections)

    assert len(chunks) == 1
    assert chunks[0].text == "Short body."
    assert chunks[0].heading_path == "Root > A. First"


def test_every_chunk_keeps_the_full_heading_path():
    """A citation names the section a reviewer must open. A split that drops the path on
    the second half makes half the corpus uncitable."""
    long_text = " ".join(f"Sentence number {i} about coverage." for i in range(200))
    sections = [Section(heading_path="Root > B. Covered", text=long_text)]

    chunks = chunk_sections(sections, max_chars=400)

    assert len(chunks) > 1
    assert all(c.heading_path == "Root > B. Covered" for c in chunks)


def test_ordinals_are_contiguous_across_sections():
    sections = [
        Section(heading_path="Root > A", text="First body."),
        Section(heading_path="Root > B", text="Second body."),
    ]

    chunks = chunk_sections(sections)

    assert [c.ordinal for c in chunks] == [0, 1]


def test_no_chunk_exceeds_the_limit():
    long_text = " ".join(f"Sentence {i} runs on for a while here." for i in range(300))
    sections = [Section(heading_path="Root > C", text=long_text)]

    chunks = chunk_sections(sections, max_chars=500)

    assert all(len(c.text) <= 500 for c in chunks)


def test_splits_overlap_so_a_criterion_is_not_cut_in_half():
    """A numeric criterion sitting on a split boundary would otherwise appear in neither
    chunk in full, and retrieval would never score it."""
    text = "A. " + ("filler " * 100) + "AHI greater than or equal to 15 events per hour. " + ("tail " * 100)
    sections = [Section(heading_path="Root > D", text=text)]

    chunks = chunk_sections(sections, max_chars=400, overlap_chars=200)

    assert any("15 events per hour" in c.text for c in chunks)


def test_empty_input_produces_no_chunks():
    assert chunk_sections([]) == []


def test_whitespace_only_section_is_dropped():
    assert chunk_sections([Section(heading_path="Root", text="   ")]) == []


def test_overlap_must_be_smaller_than_the_window():
    """Overlap at or above the window size never advances the cursor, so the splitter
    would loop forever building identical chunks."""
    with pytest.raises(ValueError, match="overlap"):
        chunk_sections(
            [Section(heading_path="Root", text="x" * 100)], max_chars=100, overlap_chars=100
        )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/policy && uv run pytest tests/test_chunking.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'policy.chunking'`.

- [ ] **Step 3: Write `chunking.py`**

```python
"""Sections into retrievable chunks.

Splitting follows the document's own headings rather than a fixed window, so every chunk
keeps the heading path a reviewer would use to find it. A citation that names
"Nationally Covered Indications" can be opened; one that names "chunk 47" cannot."""

import re
from dataclasses import dataclass

from policy.parsing import Section

_SENTENCE_END = re.compile(r"(?<=[.;:])\s+")


@dataclass(frozen=True)
class ChunkRecord:
    ordinal: int
    heading_path: str
    text: str


def _split(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    pieces: list[str] = []
    window: list[str] = []
    length = 0
    for sentence in _SENTENCE_END.split(text):
        if length + len(sentence) + 1 > max_chars and window:
            pieces.append(" ".join(window))
            # Carry the tail of the window forward so a criterion sitting on the boundary
            # appears whole in at least one chunk.
            carried: list[str] = []
            carried_len = 0
            for previous in reversed(window):
                if carried_len + len(previous) > overlap_chars:
                    break
                carried.insert(0, previous)
                carried_len += len(previous) + 1
            window = carried
            length = carried_len
        window.append(sentence)
        length += len(sentence) + 1
    if window:
        pieces.append(" ".join(window))

    # A single sentence longer than the window cannot be split on a boundary, so cut it.
    final: list[str] = []
    for piece in pieces:
        while len(piece) > max_chars:
            final.append(piece[:max_chars])
            piece = piece[max_chars - overlap_chars :]
        if piece:
            final.append(piece)
    return final


def chunk_sections(
    sections: list[Section], max_chars: int = 1200, overlap_chars: int = 150
) -> list[ChunkRecord]:
    if overlap_chars >= max_chars:
        # The cursor would never advance, so the splitter would loop forever emitting
        # identical chunks. Fail loudly instead.
        raise ValueError(
            f"overlap_chars must be smaller than max_chars, got {overlap_chars} >= {max_chars}"
        )

    chunks: list[ChunkRecord] = []
    for section in sections:
        body = section.text.strip()
        if not body:
            continue
        for piece in _split(body, max_chars, overlap_chars):
            piece = piece.strip()
            if piece:
                chunks.append(
                    ChunkRecord(
                        ordinal=len(chunks), heading_path=section.heading_path, text=piece
                    )
                )
    return chunks
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/policy && uv run pytest tests/test_chunking.py -v && uv run ruff check .`
Expected: PASS, 8 tests, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/policy
git commit -m "chunk sections along headings, keeping the heading path"
```

---

### Task 6: Effective-date resolution

**Files:**
- Create: `services/policy/src/policy/dating.py`
- Test: `services/policy/tests/test_dating.py`

**Interfaces:**
- Consumes: nothing (pure)
- Produces:
  - `Versioned` — `Protocol` with `effective_from: date` and `effective_to: date | None`
  - `in_force_on(versions: Sequence[V], on: date) -> V | None`

- [ ] **Step 1: Write the failing test**

Create `services/policy/tests/test_dating.py`:

```python
from dataclasses import dataclass
from datetime import date

from policy.dating import in_force_on


@dataclass(frozen=True)
class Version:
    name: str
    effective_from: date
    effective_to: date | None


V1 = Version("v1", date(2008, 3, 13), date(2019, 12, 31))
V2 = Version("v2", date(2020, 1, 1), None)


def test_selects_the_version_covering_the_date():
    assert in_force_on([V1, V2], date(2015, 6, 1)) is V1
    assert in_force_on([V1, V2], date(2026, 6, 1)) is V2


def test_effective_from_is_inclusive():
    assert in_force_on([V1, V2], date(2020, 1, 1)) is V2


def test_effective_to_is_inclusive():
    """CMS states an end date as the last day the version applies, not the first day it
    does not. Treating it as exclusive silently adjudicates that day's claims against the
    following version."""
    assert in_force_on([V1, V2], date(2019, 12, 31)) is V1


def test_a_date_before_any_version_has_no_policy():
    """No policy in force is not the same as the earliest policy. A case dated before the
    determination existed must escalate, not be judged by a rule that did not yet apply."""
    assert in_force_on([V1, V2], date(2001, 1, 1)) is None


def test_open_ended_version_covers_the_future():
    assert in_force_on([V2], date(2099, 1, 1)) is V2


def test_gap_between_versions_yields_nothing():
    a = Version("a", date(2010, 1, 1), date(2010, 6, 30))
    b = Version("b", date(2011, 1, 1), None)

    assert in_force_on([a, b], date(2010, 9, 1)) is None


def test_overlapping_versions_pick_the_latest_start():
    """CMS occasionally publishes overlapping ranges. The later determination is the one
    that governs; picking arbitrarily would make the result depend on row order."""
    old = Version("old", date(2010, 1, 1), None)
    new = Version("new", date(2015, 1, 1), None)

    assert in_force_on([old, new], date(2020, 1, 1)) is new
    assert in_force_on([new, old], date(2020, 1, 1)) is new


def test_no_versions_yields_nothing():
    assert in_force_on([], date(2020, 1, 1)) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/policy && uv run pytest tests/test_dating.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'policy.dating'`.

- [ ] **Step 3: Write `dating.py`**

```python
"""Choosing the version of a policy that governs a given date of service.

A case is adjudicated against the policy in force on the date of service, not today's
policy. Coverage determinations are revised, and judging a 2015 claim by a 2020 rule is
wrong in the direction that harms the member."""

from datetime import date
from typing import Protocol, TypeVar


class Versioned(Protocol):
    effective_from: date
    effective_to: date | None


V = TypeVar("V", bound=Versioned)


def in_force_on(versions: list[V], on: date) -> V | None:
    """The version covering `on`, or None if no version does.

    Both bounds are inclusive: CMS states an end date as the last day the version applies.
    Returning None is a real answer -- a date before any version means the determination
    did not yet exist, which must escalate rather than fall back to the earliest rule."""
    covering = [
        v
        for v in versions
        if v.effective_from <= on and (v.effective_to is None or on <= v.effective_to)
    ]
    if not covering:
        return None
    # Overlapping ranges do occur. The later determination governs; without this the
    # result would depend on row order.
    return max(covering, key=lambda v: v.effective_from)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/policy && uv run pytest tests/test_dating.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add services/policy
git commit -m "resolve which policy version governs a date of service"
```

---

### Task 7: Embedding and ingest

**Files:**
- Create: `services/policy/src/policy/embedding.py`, `src/policy/ingest.py`
- Modify: `services/policy/src/policy/main.py`
- Test: `services/policy/tests/test_ingest.py`

**Interfaces:**
- Consumes: `fetch_ncd`/`NcdRecord` (Task 3), `html_to_sections` (Task 4), `chunk_sections` (Task 5), `Policy`/`Chunk` (Task 2)
- Produces:
  - `Embedder.encode(texts: list[str]) -> list[list[float]]` — 384 dims, loaded once at startup
  - `async ingest_ncd(session, embedder, records: list[NcdRecord]) -> IngestResult`
  - `IngestResult` — frozen dataclass `(policies_added: int, chunks_added: int, skipped: int)`
  - `POST /ingest` accepting `{"ncd_id": "226"}`, returning the counts

Ingest is **idempotent**: a `(document_id, document_version)` already stored is skipped, not duplicated.

- [ ] **Step 1: Write the failing test**

Create `services/policy/tests/test_ingest.py`. These use a real database — the async session is the thing under test, and a mock would prove nothing about the unique constraint.

```python
import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from policy.cms import parse_ncd_response
from policy.ingest import ingest_ncd
from policy.models import Chunk, Policy

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ncd-226.json").read_text())


class StubEmbedder:
    """Deterministic and instant. The embedder is exercised for real in Task 8; here it
    would only make the test slow and flaky."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7)] * 384 for t in texts]


@pytest.fixture
async def session(db_session):
    return db_session


async def test_ingest_stores_the_policy_and_its_chunks(session):
    result = await ingest_ncd(session, StubEmbedder(), parse_ncd_response(FIXTURE))

    assert result.policies_added == 1
    assert result.chunks_added > 0

    policy = (await session.execute(select(Policy))).scalar_one()
    assert policy.display_id == "240.4"
    assert policy.effective_from == date(2008, 3, 13)
    assert policy.effective_to is None


async def test_ingest_is_idempotent(session):
    records = parse_ncd_response(FIXTURE)
    first = await ingest_ncd(session, StubEmbedder(), records)
    second = await ingest_ncd(session, StubEmbedder(), records)

    assert second.policies_added == 0
    assert second.skipped == 1
    assert second.chunks_added == 0

    chunk_count = (await session.execute(select(func.count()).select_from(Chunk))).scalar_one()
    assert chunk_count == first.chunks_added


async def test_every_chunk_carries_a_heading_path(session):
    await ingest_ncd(session, StubEmbedder(), parse_ncd_response(FIXTURE))

    chunks = (await session.execute(select(Chunk))).scalars().all()
    assert chunks
    assert all(c.heading_path.strip() for c in chunks)


async def test_chunks_are_removed_with_their_policy(session):
    """Chunks outliving their policy would be retrievable and uncitable."""
    await ingest_ncd(session, StubEmbedder(), parse_ncd_response(FIXTURE))
    policy = (await session.execute(select(Policy))).scalar_one()

    await session.delete(policy)
    await session.flush()

    remaining = (await session.execute(select(func.count()).select_from(Chunk))).scalar_one()
    assert remaining == 0
```

Create `services/policy/tests/conftest.py` providing the `db_session` fixture: an async session against `pramana_policy` opened in a transaction and rolled back after each test, so tests leave no rows behind.

```python
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from policy.models import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://pramana:pramana@localhost:5433/pramana_policy",
)


@pytest.fixture(scope="session")
def engine():
    return create_async_engine(TEST_DATABASE_URL)


@pytest.fixture
async def db_session(engine):
    """Each test runs inside a transaction that is rolled back, so tests never see each
    other's rows and the corpus is not left behind for the next run."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with factory() as session:
            yield session
        await transaction.rollback()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/policy && uv run pytest tests/test_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'policy.ingest'`.

- [ ] **Step 3: Write `embedding.py`**

```python
"""Local embedding.

The model is loaded once at startup rather than per request: loading it costs seconds, and
paying that on the first search blows past the caller's timeout."""

from fastembed import TextEmbedding

from policy.config import get_settings

DIMENSIONS = 384


class Embedder:
    def __init__(self, model_name: str | None = None) -> None:
        self._model = TextEmbedding(model_name or get_settings().embedding_model)

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(texts)]
```

- [ ] **Step 4: Write `ingest.py`**

```python
"""Fetch, parse, chunk, embed, store.

Idempotent by (document_id, document_version): re-running ingest for a version already
stored is a no-op. Ingest runs on a schedule and after failures, so "run it again" must be
safe rather than a way to double the corpus."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from policy.chunking import chunk_sections
from policy.cms import NcdRecord
from policy.models import Chunk, Policy
from policy.parsing import html_to_sections

#: Payload field name to the heading a reader would recognise it by.
SECTION_HEADINGS = {
    "item_service_description": "Item/Service Description",
    "indications_limitations": "Indications and Limitations of Coverage",
    "cross_reference": "Cross Reference",
    "reasons_for_denial": "Reasons for Denial",
    "other_text": "Other",
}


@dataclass(frozen=True)
class IngestResult:
    policies_added: int
    chunks_added: int
    skipped: int


async def ingest_ncd(
    session: AsyncSession, embedder, records: list[NcdRecord]
) -> IngestResult:
    policies_added = chunks_added = skipped = 0

    for record in records:
        existing = await session.execute(
            select(Policy.id).where(
                Policy.document_id == record.document_id,
                Policy.document_version == record.document_version,
            )
        )
        if existing.scalar_one_or_none() is not None:
            skipped += 1
            continue

        policy = Policy(
            document_id=record.document_id,
            document_version=record.document_version,
            display_id=record.display_id,
            title=record.title,
            effective_from=record.effective_from,
            effective_to=record.effective_to,
            benefit_category=record.benefit_category,
            source_url=record.source_url,
        )
        session.add(policy)
        await session.flush()
        policies_added += 1

        sections = []
        for field, raw_html in record.sections_html.items():
            sections.extend(
                html_to_sections(raw_html, root_heading=SECTION_HEADINGS.get(field, field))
            )

        chunks = chunk_sections(sections)
        if not chunks:
            continue

        vectors = embedder.encode([c.text for c in chunks])
        for chunk, vector in zip(chunks, vectors, strict=True):
            session.add(
                Chunk(
                    policy_id=policy.id,
                    ordinal=chunk.ordinal,
                    heading_path=chunk.heading_path,
                    text=chunk.text,
                    embedding=vector,
                )
            )
        chunks_added += len(chunks)
        await session.flush()

    return IngestResult(policies_added, chunks_added, skipped)
```

- [ ] **Step 5: Add the route**

Replace `services/policy/src/policy/main.py` with:

```python
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from policy.cms import fetch_ncd
from policy.config import get_settings
from policy.db import SessionFactory
from policy.embedding import Embedder
from policy.ingest import ingest_ncd


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Loading the model costs seconds. Paying it at startup keeps it off the first
    # caller's timeout, which is where it would otherwise land.
    app.state.embedder = Embedder()
    yield


app = FastAPI(title="pramana policy", lifespan=lifespan)


class IngestRequest(BaseModel):
    ncd_id: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/ingest")
async def ingest(request: IngestRequest) -> dict[str, int]:
    async with httpx.AsyncClient(
        base_url=get_settings().cms_base_url, timeout=30
    ) as client:
        records = await fetch_ncd(client, request.ncd_id)

    async with SessionFactory() as session:
        result = await ingest_ncd(session, app.state.embedder, records)
        await session.commit()

    return {
        "policies_added": result.policies_added,
        "chunks_added": result.chunks_added,
        "skipped": result.skipped,
    }
```

Note `test_health.py` from Task 1 still passes — `TestClient(app)` as a context manager runs the lifespan, so the embedder loads once during that test.

- [ ] **Step 6: Run the tests to verify they pass**

Run:

```bash
cd services/policy
DATABASE_URL=postgresql+asyncpg://pramana:pramana@localhost:5433/pramana_policy \
  uv run pytest tests/test_ingest.py -v
```

Expected: PASS, 4 tests.

- [ ] **Step 7: Ingest the real corpus and verify**

```bash
DB_PORT=5433 REDIS_PORT=6380 docker compose up -d --build policy
curl -s -X POST localhost:8001/ingest -H 'Content-Type: application/json' -d '{"ncd_id":"226"}'
curl -s -X POST localhost:8001/ingest -H 'Content-Type: application/json' -d '{"ncd_id":"330"}'
docker compose exec -T db psql -U pramana -d pramana_policy \
  -c "select display_id, effective_from, effective_to from policies order by display_id;" \
  -c "select count(*) from chunks;"
```

Expected: `240.4` and `240.4.1` present, chunk count greater than zero. Record the real numbers in the commit body — they are the first corpus statistics the project has.

- [ ] **Step 8: Commit**

```bash
git add services/policy
git commit -m "ingest coverage determinations into the corpus"
```

---

### Task 8: Hybrid retrieval and search

**Files:**
- Create: `services/policy/src/policy/retrieval.py`
- Modify: `services/policy/src/policy/main.py`
- Test: `services/policy/tests/test_retrieval.py`

**Interfaces:**
- Consumes: `Chunk`, `Policy`, `Embedder`, `in_force_on`
- Produces:
  - `reciprocal_rank_fusion(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]` — **pure**
  - `async search(session, embedder, reranker, query: str, on: date | None, limit: int = 5) -> list[Hit]`
  - `Hit` — pydantic model in `packages/common` terms: `chunk_id`, `policy_id`, `display_id`, `heading_path`, `text`, `score`
  - `POST /search` accepting `{"query": str, "date_of_service": "YYYY-MM-DD" | null, "limit": int}`

**Carried decision:** the cross-encoder stays even though it costs ranking accuracy, because it is the only stage producing a score a threshold can be compared against. See `docs/decisions/0007-reranker-produces-the-score.md`. Do not remove it to improve the retrieval numbers.

- [ ] **Step 1: Write the failing test**

Create `services/policy/tests/test_retrieval.py`:

```python
from policy.retrieval import reciprocal_rank_fusion


def test_fuses_two_rankings():
    dense = [10, 20, 30]
    lexical = [30, 10, 40]

    fused = reciprocal_rank_fusion([dense, lexical])
    order = [chunk_id for chunk_id, _ in fused]

    assert order[0] == 10
    assert set(order) == {10, 20, 30, 40}


def test_a_document_ranked_well_by_both_beats_one_ranked_well_by_either():
    """This is the whole point of fusing: agreement across two different notions of
    relevance outranks a strong showing in one."""
    fused = dict(reciprocal_rank_fusion([[1, 2], [1, 3]]))

    assert fused[1] > fused[2]
    assert fused[1] > fused[3]


def test_scores_descend():
    fused = reciprocal_rank_fusion([[5, 6, 7], [7, 6, 5]])
    scores = [score for _, score in fused]

    assert scores == sorted(scores, reverse=True)


def test_an_empty_ranking_contributes_nothing():
    assert reciprocal_rank_fusion([[1, 2], []]) == reciprocal_rank_fusion([[1, 2]])


def test_no_rankings_yields_nothing():
    assert reciprocal_rank_fusion([]) == []


def test_fused_scores_carry_no_relevance_information():
    """The top-ranked chunk always scores 1/(k+1) regardless of whether it is relevant,
    which is exactly why the gate cannot threshold on an RRF score and the cross-encoder
    has to stay. See docs/decisions/0007."""
    one = reciprocal_rank_fusion([[42]])
    other = reciprocal_rank_fusion([[99]])

    assert one[0][1] == other[0][1]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd services/policy && uv run pytest tests/test_retrieval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'policy.retrieval'`.

- [ ] **Step 3: Write `reciprocal_rank_fusion`**

```python
"""Hybrid retrieval: dense similarity, lexical matching, fused, then reranked.

Dense search understands meaning; lexical search catches exact tokens like "AHI" and
"Type IV" that embeddings blur. Fusing them uses agreement between two different notions
of relevance."""

from collections import defaultdict


def reciprocal_rank_fusion(
    rankings: list[list[int]], k: int = 60
) -> list[tuple[int, float]]:
    """Combine rankings by reciprocal rank.

    The score depends only on position, never on the underlying similarity, so it says
    nothing about whether the top result is actually relevant. That is why the escalation
    gate is built on the cross-encoder score instead -- see docs/decisions/0007."""
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for position, chunk_id in enumerate(ranking):
            scores[chunk_id] += 1.0 / (k + position + 1)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/policy && uv run pytest tests/test_retrieval.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Add `Hit` to the shared package**

`Hit` crosses a service boundary — the adjudication service will consume `/search` in plan 04 — so it belongs in the coupling point, not in this service. Append to `packages/common/src/pramana_common/schemas.py`:

```python
class Hit(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: int
    policy_id: int
    #: The number a human cites, e.g. "240.4".
    display_id: str
    heading_path: str
    text: str
    #: The cross-encoder's score, not the fused score. RRF scores carry no relevance
    #: information, so this is the only value downstream stages can threshold on.
    score: float
```

- [ ] **Step 6: Add the reranker**

Append to `services/policy/src/policy/embedding.py`:

```python
class Reranker:
    """The cross-encoder.

    It is here to produce a score a threshold can be compared against, not to improve
    ranking -- it measurably costs ranking accuracy. See docs/decisions/0007. Removing it
    would improve the retrieval table and destroy the ability to refuse."""

    def __init__(self, model_name: str | None = None) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model_name or get_settings().rerank_model)

    def score(self, query: str, documents: list[str]) -> list[float]:
        return [float(s) for s in self._model.rerank(query, documents)]
```

- [ ] **Step 7: Add dense, lexical and the search function**

Append to `services/policy/src/policy/retrieval.py`:

```python
from collections import defaultdict
from datetime import date

from pramana_common.schemas import Hit
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from policy.dating import in_force_on
from policy.models import Chunk, Policy

#: How many chunks survive fusion into reranking. Wider costs latency; narrower drops
#: documents the cross-encoder would have promoted.
CANDIDATES = 20


async def _dense(session: AsyncSession, vector: list[float], limit: int) -> list[int]:
    rows = await session.execute(
        select(Chunk.id).order_by(Chunk.embedding.cosine_distance(vector)).limit(limit)
    )
    return list(rows.scalars())


async def _lexical(session: AsyncSession, query: str, limit: int) -> list[int]:
    """Catches exact tokens like "AHI" and "Type IV" that embeddings blur."""
    tsquery = func.plainto_tsquery("english", query)
    rows = await session.execute(
        select(Chunk.id)
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(func.ts_rank(Chunk.tsv, tsquery).desc())
        .limit(limit)
    )
    return list(rows.scalars())


async def search(
    session: AsyncSession,
    embedder,
    reranker,
    query: str,
    on: date | None = None,
    limit: int = 5,
) -> list[Hit]:
    vector = embedder.encode([query])[0]
    fused = reciprocal_rank_fusion(
        [
            await _dense(session, vector, CANDIDATES),
            await _lexical(session, query, CANDIDATES),
        ]
    )
    ids = [chunk_id for chunk_id, _ in fused[:CANDIDATES]]
    if not ids:
        return []

    rows = (
        await session.execute(
            select(Chunk, Policy)
            .join(Policy, Chunk.policy_id == Policy.id)
            .where(Chunk.id.in_(ids))
        )
    ).all()

    if on is not None:
        # Keep only chunks belonging to the version that governed the date of service. A
        # case judged by a policy that was not yet in force is wrong in the direction that
        # harms the member.
        versions: dict[str, dict[int, Policy]] = defaultdict(dict)
        for _, policy in rows:
            versions[policy.display_id][policy.id] = policy
        governing = {
            display: in_force_on(list(found.values()), on)
            for display, found in versions.items()
        }
        rows = [
            (chunk, policy)
            for chunk, policy in rows
            if (winner := governing.get(policy.display_id)) is not None
            and winner.id == policy.id
        ]

    if not rows:
        return []

    scores = reranker.score(query, [chunk.text for chunk, _ in rows])
    ranked = sorted(zip(rows, scores, strict=True), key=lambda pair: -pair[1])[:limit]

    return [
        Hit(
            chunk_id=chunk.id,
            policy_id=policy.id,
            display_id=policy.display_id,
            heading_path=chunk.heading_path,
            text=chunk.text,
            score=score,
        )
        for (chunk, policy), score in ranked
    ]
```

Then add to `main.py`: construct one `Reranker` in the lifespan alongside the `Embedder`, and a `POST /search` taking `{"query": str, "date_of_service": date | None, "limit": int = 5}` that opens a session, calls `search`, and returns the hits.

The score returned is the reranker's, not the fused score — downstream stages threshold on it.

- [ ] **Step 8: Add `POST /search` and verify against the real corpus**

```bash
DB_PORT=5433 REDIS_PORT=6380 docker compose up -d --build policy
curl -s -X POST localhost:8001/search -H 'Content-Type: application/json' \
  -d '{"query":"what AHI qualifies a patient for CPAP","date_of_service":"2026-01-15","limit":3}' \
  | python3 -m json.tool
```

Expected: hits whose `heading_path` names the covered-indications section and whose text contains the AHI criteria. Paste the real output into the report.

Also verify the date filter does something:

```bash
curl -s -X POST localhost:8001/search -H 'Content-Type: application/json' \
  -d '{"query":"what AHI qualifies a patient for CPAP","date_of_service":"2001-01-01","limit":3}'
```

Expected: **no hits** — NCD 240.4 was not in force in 2001. If this returns hits, the effective-date filter is not wired in, and every downstream stage would be adjudicating against policy that did not yet exist.

- [ ] **Step 9: Commit**

```bash
git add services/policy
git commit -m "add hybrid retrieval over the policy corpus

Dense and lexical rankings fused with RRF, then reranked. The returned score is
the cross-encoder's, because RRF scores carry no relevance information and the
escalation gate has to threshold on something."
```

---

## Self-Review

**Spec coverage.** This plan implements the design's §3 `policy` service row, §3 effective-dated versioning, §8 corpus sourcing, and the retrieval half of §4 step 3. It does **not** cover: member records (plan 03), criteria extraction and the pipeline (plan 04), auth and gateway (plan 05), evals (plan 06), console (plan 07).

**Placeholder scan.** No `TBD`/`TODO`. Every code step carries literal code. The only prose-specified work is the final `POST /search` handler (Task 8 step 8), a short call into the fully-specified `search()` above it, immediately followed by two verification commands with exact expected output — including a negative case that fails loudly if the effective-date filter was never wired in.

**Note on `Hit`.** It is added to `packages/common` rather than to this service, because the adjudication service consumes `/search` in plan 04 and `packages/common` is the single coupling point. That is the one place plan 02 touches the shared package.

**Type consistency.** `Section(heading_path, text)` is defined in Task 4 and consumed with those names in Task 5. `ChunkRecord(ordinal, heading_path, text)` from Task 5 is consumed in Task 7. `NcdRecord.sections_html` (Task 3) is keyed by payload field name, and Task 7's `SECTION_HEADINGS` maps exactly those keys. `Policy.effective_from`/`effective_to` (Task 2) satisfy the `Versioned` protocol (Task 6). `Embedder.encode` (Task 7) matches `StubEmbedder.encode` in its own tests.

**Known open question, not resolved by this plan.** NCD 240.4's criteria are a boolean expression, not a flat list — "AHI ≥ 15 **or** (AHI 5–14 **and** documented symptoms **or** comorbidities)". The merged `evaluate_gate` requires *all* criteria met and cannot express that. This does not block plan 02, which never touches criteria structure, but it **must be settled before plan 04**. Recorded in `.workspace/STATE.md`.
