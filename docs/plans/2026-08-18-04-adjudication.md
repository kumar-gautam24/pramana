# Pramana Plan 04 — Adjudication Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `adjudication` service — the seven-stage pipeline that turns a prior-authorization request into an `APPROVE` or an `ESCALATE`, with every criterion's verdict and its evidence recorded.

**Architecture:** A FastAPI service owning `pramana_adjudication`, layered as `routers → services → repositories → models` with pure logic in `domain/` (ADR-0013, sweep S1). A case is enqueued on Redis Streams and processed by a worker that calls `policy` for the governing determination and its criteria text, and `member` for the facts each criterion needs. Every stage appends to an append-only `case_events` log and publishes to Redis so the console can render the pipeline live.

**Tech Stack:** Python 3.12, uv, FastAPI, asyncpg, Postgres 16, Redis (Streams + Pub/Sub), httpx, Ollama (Qwen2.5-14B-Instruct via the provider abstraction), pytest, ruff.

## Global Constraints

- Python 3.12; raw SQL over asyncpg; numbered `.sql` migrations applied by `run_migrations` (no ORM, no Alembic).
- The service owns `pramana_adjudication` and no other database. It reaches `policy` and `member` **only** over HTTP.
- `packages/common` is the sole shared import: `Outcome`, `Verdict`, `GateReason`, `CriterionType`, `CriterionResult`, `GateThresholds`, `evaluate_gate`, `Hit`, `Criterion`, `CriterionOutcome`, `Determination`.
- **No deny path.** `Outcome` has two members and `evaluate_gate` is not modified. Anything that would emit a third outcome is wrong (ADR-0002).
- **AI decides what the rules are; deterministic code checks the facts** (ADR-0003). The model extracts and classifies criteria and emits parameters; it never performs a comparison, a date calculation or a count.
- **No hardcoded per-policy logic.** Nothing may branch on `240.4`, an NCD id, or a CPT code. Policy is data.
- `case_events` is **append-only** — never updated, never deleted.
- Misconfiguration fails at startup: the lifespan probes the database, Redis, and both upstream services before serving.
- Comments explain **why**, never what.
- Commits: conventional, imperative, lowercase subject. **Never any attribution trailer.**
- Tests on critical paths: criteria-set aggregation, each verifier, the pipeline's stage ordering and short-circuits, and the append-only guarantee.

## What already exists

| | |
| --- | --- |
| `packages/common` | the gate, its vocabulary, and the wire schemas — 45 tests |
| `policy` (8001) | `POST /search` returning `Hit`s, effective-dated, cross-encoder scored — 93 tests |
| `member` (8005) | coverage, sleep studies, conditions, adherence, notes — 91 tests |
| `scripts/migrate.py` | applies a service's migrations |

Read `services/policy/src/policy/` before starting: it is the reference for layout, the lifespan order (`probe` then pool), the migration runner, and the router/service/repository split.

## The two design decisions this plan implements

**Alternative criteria sets (ADR-0011).** A coverage policy is a boolean expression, not a flat list. NCD 240.4 approves on AHI ≥ 15, **or** on AHI 5–14 **with** symptoms **or** with a qualifying comorbidity. Extraction therefore emits one or more **criteria sets** in disjunctive normal form; the policy is satisfied if **any one set** is fully met. `evaluate_gate` runs once per set, unchanged. On escalation the system reports the blocking criteria of the **closest** set — fewest unmet — because a reviewer needs to know which single document would settle the case, not the failures of a path the member was never on.

**The verifier split.** `threshold`, `enum` and `temporal` criteria are answered by calling `member`'s factual endpoints and comparing in Python. `judgment` criteria retrieve from the member's notes and ask the model. The model supplies the parameters for the first three; it never evaluates them.

---

### Task 1: Service skeleton

**Files:** `services/adjudication/{pyproject.toml,Dockerfile}`, `src/adjudication/{__init__,config,db,main}.py`, `src/adjudication/routers/health.py`, `tests/{conftest.py,test_health.py,test_router_wiring.py}`; modify `docker-compose.yml`, `scripts/create-databases.sql`.

Mirror `services/policy` exactly: `db.py` carries `pool()` (`min_size=0`), `migration_pool()` is **not** needed (no vector codec), `probe()`, and `run_migrations`. The lifespan probes before opening the pool.

`config.py` requires `database_url`, `redis_url`, `policy_url`, `member_url`, `llm_model`, and the gate's `min_confidence` — none defaulted except `min_confidence`.

- [ ] Health test first, then the package, then compose on `${ADJUDICATION_PORT:-8002}` with `pramana_adjudication_test` added to the bootstrap SQL.
- [ ] Router-wiring tests in the shape of `services/policy/tests/test_router_wiring.py` — a 422 proves the route resolved; a 404 with FastAPI's default body proves it did not.
- [ ] Verify the container answers `/health` and `/ready`, and that a bad `DATABASE_URL` fails startup.
- [ ] Commit.

---

### Task 2: Schema and migration

**Files:** `migrations/0001_cases_and_determinations.sql`, `src/adjudication/models/`, `tests/test_models.py`.

```
cases              id, member_id, requested_code, icd10, date_of_service,
                   kind ('initial'|'continuation'), status, created_at
criteria           id, case_id, set_ordinal, ordinal, text, type, params jsonb,
                   source_chunk_id, source_display_id
criterion_results  id, criterion_id, verdict, confidence, tool, evidence jsonb
determinations     id, case_id, outcome, reason, blocking jsonb, thresholds jsonb,
                   winning_set int null, created_at
reviews            id, case_id, clinician_id, outcome, rationale,
                   agreed_with_system bool, created_at
case_events        id, case_id, seq, type, payload jsonb, created_at
```

- `criteria.set_ordinal` is what makes ADR-0011 representable: criteria belonging to the same alternative set share it.
- `determinations.winning_set` is NULL on escalation and names the satisfied set on approval — the audit answer to "which path approved this".
- `case_events` gets `UNIQUE (case_id, seq)` so a gap or a duplicate is a constraint violation rather than a silent reordering, and a `BEFORE UPDATE OR DELETE` trigger that raises. Append-only must be enforced by the database, not by convention: a commissioner's audit rests on it.
- Every FK `ON DELETE CASCADE` **except `case_events.case_id`, which is `ON DELETE RESTRICT`.** The two requirements above cannot both hold for that one table: a cascading delete of a case reaches `case_events`, where the append-only trigger raises, so the delete fails either way. What differs is the error a reader gets. Making it explicit says the true thing — a case whose audit trail exists cannot be deleted, which is the point of keeping one. The trigger stays as the second line of defence, and covers `TRUNCATE` too: `TRUNCATE` bypasses row triggers entirely, so it needs its own `BEFORE TRUNCATE ... FOR EACH STATEMENT` trigger. An audit log a `TRUNCATE` empties is not an audit log.
- `cases.status` tracks pipeline progress only — `queued`, `running`, `decided`, `failed`. The outcome lives on the determination and is never mirrored here; two copies of a decision drift, and only one of them is the one a regulator reads.
- No foreign key crosses a service boundary: `criteria.source_chunk_id` and `source_display_id` belong to `policy`, `cases.member_id` to `member`, `reviews.clinician_id` to `auth`. They are recorded as values, unreferenced.
- A case may be adjudicated more than once, so `determinations` carries no unique constraint on `case_id` — a superseded determination must survive. The current one is the newest by `created_at`, then `id`.

- [ ] Tests asserting: the append-only trigger rejects an UPDATE, a DELETE and a TRUNCATE; `(case_id, seq)` is unique; `winning_set` is nullable; deleting a case cascades its four other child tables empty; deleting a case that has events is refused.
- [ ] Apply via `scripts/migrate.py`; `pg_dump --schema-only` the result and paste it.
- [ ] Commit.

---

### Task 3: Criteria sets — the pure aggregation layer

**Files:** `src/adjudication/domain/criteria_sets.py`, `tests/test_criteria_sets.py`. **Pure — no I/O.**

**Interfaces:**
- `CriteriaSet` — frozen: `ordinal: int`, `criteria: tuple[Criterion, ...]`
- `SetOutcome` — frozen: `ordinal: int`, `decision: GateDecision`, `unmet: tuple[str, ...]`
- `aggregate(sets: list[CriteriaSet], results: dict[str, CriterionResult], thresholds: GateThresholds) -> AggregateDecision`
- `AggregateDecision` — frozen: `outcome: Outcome`, `reason: GateReason | None`, `winning_set: int | None`, `blocking: tuple[str, ...]`, `closest_set: int | None`

Rules, each of which gets a test that fails without it:

1. Run `evaluate_gate` **once per set** over that set's results. Do not reimplement it.
2. If **any** set approves → `APPROVE`, `winning_set` = that set's ordinal. On ties, the **lowest** ordinal wins, so the result does not depend on dict order — the same audit-reproducibility argument as `in_force_on`'s tiebreak.
3. If none approves → `ESCALATE`, and `blocking`/`reason` come from the **closest** set: fewest unmet criteria, ties broken by lowest ordinal.
4. **No sets at all** → `ESCALATE` with `no_criteria`. Extraction producing nothing is a failure to understand the policy, never a policy with no requirements.
5. A set referencing a criterion with no result → raise. A silently-missing verification must not become an approval.

- [ ] Exhaustive test that no combination of sets and results yields anything but `APPROVE` or `ESCALATE` — ADR-0002 at this layer too.
- [ ] Test that a member satisfying only set 2 approves, with `winning_set == 2`.
- [ ] Test that the reported blocking criteria belong to the closest set, using an asymmetric case (set 1 missing three, set 2 missing one).
- [ ] Commit.

---

### Task 4: Upstream clients

**Files:** `src/adjudication/services/{policy_client,member_client}.py`, `tests/test_clients.py`.

Thin, typed `httpx` wrappers. `policy_client.search(query, date_of_service, limit) -> list[Hit]`. `member_client` exposes one method per factual endpoint, each returning a frozen dataclass.

- **A 5xx or a timeout from either upstream must escalate the case, never approve it.** Raise a typed `UpstreamUnavailable`; the pipeline turns it into `ESCALATE` with an explicit reason. An approval built on an unreachable member service is exactly the failure this project exists to prevent.
- `member_client.coverage` must distinguish **404 (no record)** from `{"active": false}` (record, no coverage) — they route to opposite outcomes. Return an explicit tri-state, not a bool.
- Tests use a stub transport; no live service.
- [ ] Commit.

---

### Task 5: Criteria extraction

**Files:** `src/adjudication/services/extract.py`, `src/adjudication/prompts/`, `tests/test_extract.py`.

Given the governing policy's chunks (from `policy_client.search`), the model returns criteria sets as **schema-constrained JSON**: for each set, a list of criteria with `text`, `type`, `params`, and `source_chunk_id`.

- **`source_chunk_id` is constrained to the ids actually retrieved**, exactly as the answer service did in the predecessor project. A criterion that cannot be traced to policy text is one the system must not act on.
- **Validate every emitted `type` against `CriterionType`** and every `params` shape against what that type's verifier requires. A malformed criterion escalates the case; it never silently becomes a `judgment` criterion, which would hand a deterministic check to the model — the ADR-0003 violation that is hardest to see.
- Tests run against a **recorded** model response fixture plus hand-written malformed ones. No live model in tests.
- [ ] Test: an unknown `type` escalates rather than defaulting.
- [ ] Test: a `source_chunk_id` outside the retrieved set is rejected.
- [ ] Commit.

---

### Task 6: Verifiers

**Files:** `src/adjudication/services/verify/{deterministic,judgment}.py`, `tests/test_verify.py`.

`verify(criterion, case, clients) -> CriterionResult` for each type.

- `threshold`, `enum`, `temporal` → fetch the fact from `member_client`, compare **in Python**, return `MET`/`NOT_MET` with confidence `1.0` and the fact as evidence. A comparison is exact; a confidence below 1.0 here would be a lie.
- `judgment` → retrieve the member's notes, ask the model, return the verdict with the model's confidence and the quoted spans.
- **A missing fact is `INSUFFICIENT_EVIDENCE`, never `NOT_MET`.** They route to different outcomes: one says the record contradicts the policy, the other says a document is missing. Conflating them tells a reviewer to look for the wrong thing.
- Tests cover each type at its boundary — the same `>=` vs `>` direction that has bitten twice in this project.
- [ ] Commit.

---

### Task 7: The pipeline and its events

**Files:** `src/adjudication/services/pipeline.py`, `src/adjudication/repositories/`, `tests/test_pipeline.py`.

The seven stages, each appending a `case_events` row before moving on. Verification of the criteria within a set runs **concurrently**; sets are independent.

Short-circuits, each tested: not eligible → `ESCALATE not_eligible`; no governing policy → `ESCALATE no_governing_policy`; extraction empty → `ESCALATE no_criteria`; upstream unavailable → `ESCALATE upstream_unavailable`. **None of these reaches the model or the gate**, and none can produce an approval.

- [ ] Test the full happy path end to end against stub clients: a member satisfying set 1 approves with `winning_set = 1` and a complete event sequence.
- [ ] Test that a near-miss member escalates naming the blocking criterion.
- [ ] Test that `case_events.seq` is contiguous from 1 with no gaps for both outcomes.
- [ ] Commit.

---

### Task 8: Worker, routes and SSE

**Files:** `src/adjudication/worker.py`, `src/adjudication/routers/{cases,events}.py`, `tests/test_routes.py`; modify `docker-compose.yml` for the worker.

- `POST /cases` → `202 {case_id}`, enqueued on a Redis Stream. Idempotent on a caller-supplied key so a retried submission does not adjudicate twice.
- `GET /cases/{id}`, `GET /cases/{id}/events` (the stored log), `GET /cases/{id}/stream` (SSE, live from Redis Pub/Sub).
- The worker consumes with a consumer group so an unacknowledged case returns after a crash.
- **The SSE stream and the stored log must render the same sequence.** A test asserts that replaying the stored events and the streamed events produce identical `seq` ordering — the audit trail and the live view are the same data or the audit claim is false.
- [ ] Commit.

---

### Task 9: End to end against the real corpus

**Files:** `tests/test_end_to_end.py`.

Using the **real** ingested policy corpus and the seeded members, with a stubbed model returning a recorded extraction:

- [ ] `p1` (AHI 46.9, valid study) approves on set 1.
- [ ] `p2` (AHI 14.446, ischemic heart disease) — the near-miss — **escalates**, and the reported blocking criterion is the AHI one, not the comorbidity. This is the case ADR-0009 exists for.
- [ ] `p4` (AHI 19.171) approves on set 1, exercising the just-over-threshold path.
- [ ] `p5` (Type IV, 2 channels) escalates on the study-validity criterion.
- [ ] A case dated 2001 escalates with `no_governing_policy`.
- [ ] Commit.

---

### Task 10: Record the state

Update `.workspace/STATE.md`, `JOURNAL.md`, `ERRORS.md` with real counts and anything that failed first time. `.workspace/` is gitignored — do not commit it.

---

## Deferred out of this plan, with an owner

- **No model is available on the development machine.** Ollama is not installed and
  nothing answers on 11434, so two things in this plan cannot be done as written. Task 5's
  fixture is **hand-authored, not recorded** — it must say so in the file itself, because a
  fixture labelled "recorded" that nobody recorded is worse than no fixture. And ADR-0010's
  startup guard ("services refuse to start if the configured model cannot produce
  schema-constrained output") is **not implemented in Task 5**: it would make the service
  unbootable here, and nothing before Task 8 calls a model. **Task 8 owns the guard.**
  Task 9's end-to-end run needs a real model and is blocked until one is installed.

- **`reviews.outcome` is deliberately unconstrained.** Unlike `determinations.outcome`,
  a clinician's review *can* be a denial — that separation is the whole reason the two
  tables are distinct. But "can be a denial" is not the same as "can be any string", and
  the column currently accepts one. The closed set is not written here because the
  vocabulary a reviewer may record (approve / deny / modify / pend for information) is a
  regulatory question the console plan answers, and guessing it now would put values in
  the schema no code has ever produced. **Plan 07 (reviewer console) closes it with a
  migration**, and must not ship with the column still open.

---

## Self-Review

**Coverage.** Implements the design's §4 pipeline, §5 adjudication tables, ADR-0011's criteria sets, and the SSE audit surface. Does not cover: auth/gateway (plan 05), evals (06), console (07).

**Placeholder scan.** Tasks 1–2 and 9 give concrete artifacts; 3–8 give exact interfaces and the named test per rule. No `TBD`.

**The risk this plan carries.** It is the first task in the project where a model's output drives control flow. The mitigations are structural rather than hopeful: schema-constrained extraction, `source_chunk_id` restricted to retrieved ids, every emitted type validated before use, comparisons performed in Python, and a gate that cannot emit a denial. Each is a test, not a prompt instruction.

**Explicitly carried forward.** `evaluate_gate` is not modified. `Hit.score` is an unbounded cross-encoder logit (0.56–7.00 observed), so any threshold is calibrated to that scale. The governing version is resolved by `policy`, never re-derived here — re-deriving it reintroduced a Critical once already.
