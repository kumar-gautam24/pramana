# Sweep S1 — Raw SQL and layered structure

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this sweep task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SQLAlchemy's declarative ORM and Alembic with raw SQL and hand-written numbered migrations, and restructure both existing services into `routers → services → repositories → models`.

**Why a sweep and not opportunistic change:** `policy` and `member` must not sit in two different styles while plans 04–07 build on both. One pass, both services, then the pattern is fixed for everything after.

**Sequencing:** runs **after plan 03 merges** and **before plan 04 starts**. Plan 04 is the largest thing in the project and it would be built twice otherwise.

**Tech Stack:** Python 3.12, uv, FastAPI, **asyncpg** (no SQLAlchemy), Postgres 16 + pgvector, pytest, ruff.

## Global Constraints

- Python 3.12. Dependencies with `uv`; lockfiles committed.
- **No ORM.** No SQLAlchemy declarative models, no session, no unit of work. Queries are SQL written out in full and executed through `asyncpg`.
- **No Alembic.** Migrations are numbered `.sql` files applied by a runner that records what it applied.
- Each service owns exactly one database. No cross-service joins.
- `packages/common` stays the only shared import. It holds **no** database code — it is wire schemas and the gate, and nothing in this sweep changes that.
- **Behaviour must not change.** This is a structural sweep. Every existing test must still pass, and where a test asserted ORM mechanics rather than behaviour, replace it with one asserting the behaviour.
- Misconfiguration still fails at startup, not on first request.
- Comments explain **why**, never what.
- Commits: conventional style, imperative, lowercase subject. **Never any attribution trailer** — no `Co-Authored-By`, no generated-with footer, no model or tool name.
- All ports free: Postgres 5432, `policy` 8001, `member` 8005.

## Target structure

Applied identically to `services/policy/src/policy/` and `services/member/src/member/`:

```
main.py           app assembly, lifespan, router registration — nothing else
config.py         settings; no default for database_url
db.py             asyncpg pool, type codecs, the migration runner
models/           frozen dataclasses — domain types, one module per aggregate
repositories/     raw SQL, one module per aggregate; the ONLY place SQL lives
services/         orchestration and business logic; imports repositories
routers/          one APIRouter per resource; imports services
domain/           pure functions, no I/O
migrations/       0001_*.sql, 0002_*.sql, ...
```

**On the two meanings of "services".** The repository's top-level `services/` holds deployable
microservices; `src/<name>/services/` holds application services. The collision is real and
mildly unfortunate. It is kept because it is the conventional layer name and the paths are
never ambiguous in practice — `services/policy/src/policy/services/ingest.py` reads oddly but
resolves unambiguously. Raise it if it grates in review.

**What goes in `domain/`.** The pure, I/O-free logic that already exists and is already well
tested: `policy`'s `parsing.py`, `chunking.py`, `dating.py`, `retrieval.reciprocal_rank_fusion`;
`member`'s `generate.py`, `notes.py`, `synthea.py`. **These modules do not change** — they move.
Their tests move with them and must pass unmodified. If a test needs editing to survive the
move, something has been changed that should not have been.

## The migration runner is the load-bearing piece

Alembic's one genuine service was tracking which migrations had run. Losing that silently is
worse than anything Alembic did wrong. The runner must:

1. Create `schema_migrations (version text primary key, applied_at timestamptz not null default now())` if absent.
2. Read `migrations/*.sql` sorted by filename.
3. Apply each unapplied file **inside a transaction**, and record its version in the same transaction. A file that fails leaves no partial state and no recorded version.
4. Be idempotent: running it twice applies nothing the second time.
5. Refuse to run if a recorded version has no corresponding file, or if an applied file's contents have changed — that means someone edited history, and continuing would leave environments silently divergent.

Point 5 is the one most likely to be skipped and the one that catches the worst failure.

---

### Task 1: The asyncpg foundation and migration runner

**Files:** create `packages/common/src/pramana_common/sqlrunner.py` **only if** both services would otherwise duplicate it verbatim; otherwise `services/policy/src/policy/db.py` and mirror into `member`.

Decide deliberately and record the reason: `packages/common` is the coupling point and a migration runner is infrastructure, not a wire contract. **Default to duplicating it** — two ~60-line files that can diverge safely beat a shared dependency that couples deploy cycles. Only share it if the duplication is literally identical and expected to stay so.

**Interfaces:**
- `pool() -> asyncpg.Pool` — created at startup, `vector` codec registered on each connection for `policy`
- `async run_migrations(pool, directory: Path) -> list[str]` — returns versions applied this run
- `async probe(pool) -> None` — `SELECT 1`, called in the lifespan so misconfiguration fails at startup

- [ ] **Step 1: Write the failing tests** covering, at minimum: applying two migrations in order; re-running applies nothing; a failing migration leaves no partial state **and** no recorded version; a recorded version whose file has vanished raises; an applied file whose contents changed raises. Use a scratch database created and dropped by the test.
- [ ] **Step 2: Run them, watch them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them, watch them pass.** Verify the checksum case by actually editing an applied file and confirming the runner refuses.
- [ ] **Step 5: Commit** — `add a migration runner that records what it applied`

---

### Task 2: `policy` — migrations and repositories

**Files:** replace `services/policy/{alembic.ini,migrations/}` with `migrations/0001_policies_and_chunks.sql`; replace `src/policy/models.py` with `src/policy/models/` dataclasses and `src/policy/repositories/`.

The existing Alembic migration is the source of truth for the SQL — transcribe it, including `CREATE EXTENSION IF NOT EXISTS vector`, the `tsv` generated column, the GIN index and `uq_policies_document_id_version`.

**The repository layer owns every query currently in `retrieval.py` and `ingest.py`**: dense search by `embedding <=> $1`, lexical search by `tsv @@ plainto_tsquery`, the governing-version lookup, and the chunk fetch. Write them as SQL.

Carry forward, because these were each fixed once and a rewrite is exactly where they come back:
- The governing version is resolved from the **full** `policies` table **before** retrieval, grouped by **`document_id`** — not `display_id`, and not from the retrieved candidates.
- The chunk fetch is ordered by `chunks.id` so tied rerank scores do not inherit row order.
- `search()` returns the **cross-encoder** score, never the fused score.

- [ ] Transcribe the migration; apply it to a fresh database and diff the resulting schema against the current one. **They must match exactly** — dump both with `pg_dump --schema-only` and compare.
- [ ] Write `models/` dataclasses and `repositories/` SQL.
- [ ] Point existing code at the repositories; delete the ORM models and Alembic tree.
- [ ] Full suite green with **no test edited except imports**. Re-ingest the corpus and confirm 2 policies / 17 chunks.
- [ ] Live check: the AHI query dated 2026-01-15 returns hits under the covered-indications heading; dated 2001-01-01 returns `[]`.
- [ ] Commit.

---

### Task 3: `policy` — routers and services

Split `main.py` into `routers/` (one `APIRouter` per resource: health, ingest, search) and `services/` (ingest orchestration, search orchestration). `main.py` keeps only app construction, the lifespan and router registration.

Move `parsing.py`, `chunking.py`, `dating.py` into `domain/` unchanged.

- [ ] Move, do not rewrite. Tests move with their modules and pass unmodified.
- [ ] `main.py` under ~40 lines.
- [ ] Full suite green; live endpoints unchanged.
- [ ] Commit.

---

### Task 4: `member` — migrations and repositories

Same shape as Task 2. Transcribe `0001_member_records.py` into SQL including `uq_cpap_usage_member_night` and every `ON DELETE CASCADE`. **Verify the cascade by executing it**, not by reading the DDL — delete a member and confirm all five child tables empty.

The flush-ordering hazard disappears with the ORM; say so in the commit body, and remove the "flush parents first" note from `.workspace/ERRORS.md` since it no longer applies.

Carry forward: coverage bounds inclusive both ends; `min_hours` caller-supplied, never defaulted in SQL; conditions and studies filtered `<= on`; adherence returns `fraction = 0.0` on zero nights rather than dividing.

- [ ] Schema diff against the current database must be exact.
- [ ] Full suite green with no test edited except imports. Re-seed and confirm 5 members, 5 studies, 150 usage nights, 10 notes, and `p2`'s AHI still above the 5–14 ceiling and below 15.
- [ ] Commit.

---

### Task 5: `member` — routers and services

Same shape as Task 3. `generate.py`, `notes.py`, `synthea.py` move into `domain/` unchanged. Routes split into `routers/`; `seed.py` becomes a service.

Carry forward: an unknown member 404s on `/coverage` rather than reporting `{"active": false}`; the other four routes keep returning empty facts for an unknown member.

- [ ] Full suite green; live endpoints unchanged including the 404 and the `min_hours` 422.
- [ ] Commit.

---

### Task 6: Prune and record

- [ ] Remove `sqlalchemy`, `alembic` and `pgvector`'s SQLAlchemy extra from both `pyproject.toml` files; `uv lock` and confirm they are gone from both lockfiles.
- [ ] `grep -rn "sqlalchemy\|alembic" services/ packages/` returns nothing outside comments.
- [ ] Update `CLAUDE.md`: raw SQL and the layered structure become stated invariants.
- [ ] Update `.workspace/STATE.md` and `JOURNAL.md`; drop the obsolete flush-ordering entry from `ERRORS.md`.
- [ ] Commit.

---

## Self-Review

**Coverage.** Implements ADR-0013 across both existing services and fixes the structure plans 04–07 will follow. Does not touch `packages/common`'s contents, the corpus, or any behaviour.

**The risk this sweep carries.** It is a large diff with no new features, which is exactly the shape in which regressions hide. Three defences: the schema must diff clean against the live database; every existing test must pass **unmodified** except for imports; and the live endpoint checks that closed each earlier branch must still produce identical output. If a test needs its assertions changed to survive, that is a behaviour change and must be justified out loud, not absorbed.

**Explicitly carried forward.** Every fixed defect named in Tasks 2, 4 and 5 was found by review once already. A rewrite of the layer they live in is the most likely place for them to return, which is why they are listed as requirements rather than left to memory.
