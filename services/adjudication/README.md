# adjudication

**The pipeline that decides a case, and the audit trail that explains it.** Port 8002, database
`pramana_adjudication`.

This is where the project's argument lives: a coverage policy is decomposed into typed criteria
by a model, each criterion is verified against the member's record by deterministic code, and a
gate that has no deny branch turns those verdicts into a determination.

Two processes share this codebase:

- **the API** (`adjudication.main`) — accepts submissions, serves cases, streams events
- **the worker** (`adjudication.worker`) — the *only* process that runs the pipeline

## Data model

| table | holds |
| --- | --- |
| `cases` | the submission: member, codes, date of service, status, run mode |
| `criteria` | the criteria extracted for a case, typed, with validated params and a source chunk id |
| `criterion_results` | one verdict per criterion, with confidence, tool and evidence |
| `determinations` | the outcome. **UNIQUE on `case_id`** — one case, one determination |
| `case_events` | append-only log, one row per stage, ordered by a per-case sequence |
| `reviews` | a clinician's own determination on an escalated case |

`case_status` moves `queued → running → decided`, or `failed` for a genuine worker crash. A
short-circuited case is always `decided` with a determination, never `failed` — a case with no
determination is invisible to the reviewer queue, which is the exact failure this system exists
to prevent.

## The pipeline

Six stages, each appending exactly one event:

```
started ─► eligibility ─► policy ─► criteria ─► criterion (× n) ─► decision
```

**Stage ordering is load-bearing, not incidental.** Every deterministic verifier assumes the
member exists — it reads an empty list from `member` as "the fact is absent" rather than "no such
member". That is only safe because eligibility is checked and short-circuited *before* any
verifier runs. Moving eligibility later would let the pipeline produce denial-shaped `NOT_MET`
answers about members it has never heard of.

The design numbered a `normalize` stage first, turning free text into codes. It is **struck, not
deferred**: a prior-auth request arrives with its codes already assigned by the submitter's
billing system, and asking a model to produce them would place a model-generated fact at the one
point nothing downstream re-checks.

### Criteria in disjunctive normal form

Extraction emits *alternative criteria sets*. NCD 240.4 is genuinely a disjunction — AHI ≥ 15
with a valid study type, **or** AHI 5–14 with documented symptoms — so a single conjunction
misreads the policy. A case is approved if any one set is fully satisfied.

On escalation the system reports the blocking criteria of the **closest** set, meaning fewest
unmet, because a reviewer needs to know which single document would have settled the case. A set
that produced no criteria at all ranks last, never first.

### The four criterion types

| type | who verifies it | how |
| --- | --- | --- |
| `threshold` | deterministic | SQL fetches the fact, Python compares (`>=`, `>`, `<=`, `<`, `==`) |
| `enum` | deterministic | SQL fetches the fact, Python tests membership |
| `temporal` | deterministic | SQL fetches the date, Python does the arithmetic |
| `judgment` | the model | retrieval plus a model call that must return a grounded span |

Model-produced parameters are validated against a closed vocabulary of facts and operators before
anything is fetched, so an unknown fact name cannot reach a database read. A fifth criterion type
added without updating that vocabulary fails a test rather than silently falling through.

### The gate

`APPROVE` requires every criterion in a set to be `met` **and** every confidence to clear the
threshold. Everything else escalates:

| reason | when |
| --- | --- |
| `criterion_not_met` | a fact contradicts the criterion |
| `insufficient_evidence` | the record does not answer the criterion |
| `low_confidence` | the answer exists but is not trusted |
| `no_criteria` | nothing checkable could be extracted |

There is no fourth outcome and no deny branch. The vocabulary lives in `packages/common` so that
no service can invent one.

### Short-circuits

Four situations end a case before it reaches the gate. Each is still recorded as a determination,
so a short-circuited case is never invisible:

| short-circuit | trigger | reason recorded |
| --- | --- | --- |
| `not_eligible` | no coverage record | `insufficient_evidence` |
| `not_eligible` | coverage inactive | `criterion_not_met` |
| `no_governing_policy` | retrieval returns nothing for the date | `no_criteria` |
| `no_criteria` | extraction produced nothing valid | `no_criteria` |
| `upstream_unavailable` | a **permanent** upstream failure | `insufficient_evidence` |

A **transient** upstream failure — a 429, a 5xx, a timeout — is deliberately *not* a
short-circuit. It says nothing about the member's record, so recording it as a determination
would put a case on a clinician's queue for a reason no clinician can act on. It propagates to
the worker instead.

## The worker

```
POST /cases ──► persist ──► XADD redis stream ──► 202 with case id
                                    │
                              worker: XREADGROUP, one case at a time
```

**One case at a time, on purpose.** Each case costs several model calls against a rate-limited
provider; a worker that saturated the limit would measure the token budget rather than the
system.

**Retries live here, not in an HTTP client.** A transient failure is retried on a bounded
backoff ladder, and *every attempt is appended to `case_events`*. That is the whole reason the
retry is at this layer: a client can wait, but only the worker can say so in the audit record. A
case sitting `running` for a minute has to be explicable while it is happening.

The ladder honours the server's own `Retry-After` when it asks for longer than the next rung —
a rate limiter knows how much of its window is left and this process does not — but the **total**
is bounded, not just each rung. A provider sending `Retry-After: 90` three times would otherwise
outlast the eval harness's per-case timeout, so the retries that exist to stop a case being
recorded as unfinished would be what caused it.

**The queue is at-least-once**, so a case may legitimately be delivered twice. The pipeline is
therefore idempotent: a case that already holds a determination returns it rather than
re-deriving it. This matters more than it sounds — criteria are stored delete-then-insert, so
re-deriving a decided case would erase the very rows its determination cites as blocking.

### Run modes and the ablation

`cases.run_mode` selects who performs the deterministic comparisons:

- `deterministic` — SQL fetches, Python compares. The shipped behaviour.
- `model_arithmetic` — SQL fetches, **the model** compares. The ablation.

The difference between the two arms lives in one module. The fetches, the evidence, the gate, the
thresholds, the events and the persistence are the same code on both sides, which is what makes a
run and its ablated twin a comparison rather than an anecdote. `run_mode` appears on the very
first event of a case, so the audit trail says which arithmetic decided it in the first place a
reader looks.

Only an operator or admin may request the ablated mode, checked here as well as at the gateway —
the gateway gates on the *route*, and this is a *field* on a route open to any session.

## The audit log and live view

Every stage appends to `case_events` and publishes the same row to Redis pub/sub. The console
subscribes over SSE at `GET /cases/{id}/stream`. **The trail a regulator inspects and the view a
reviewer watches are the same rows.**

Events commit independently of the pipeline's transactions: a stage that ran must stay in the
trail even if a later stage fails. The `decision` event is the one exception — it describes the
outcome of the transaction that persists it, so it is appended only *after* that transaction
commits. Otherwise a failed commit would leave the log permanently claiming a case approved when
no determination exists.

Determinations and their criterion results are written in one transaction, so a reviewer can
never see a determination with no evidence behind it, or evidence with no determination.

## Endpoints

| method | path | purpose |
| --- | --- | --- |
| POST | `/cases` | submit a case; returns `202` and an id |
| GET | `/cases` | the reviewer queue, filtered by outcome |
| GET | `/cases/{id}` | the case |
| GET | `/cases/{id}/criteria` | criteria with verdicts, grouped by set |
| GET | `/cases/{id}/events` | the audit trail |
| GET | `/cases/{id}/stream` | the same trail, live, over SSE |
| GET | `/cases/{id}/reviews` | clinician reviews |
| POST | `/cases/{id}/review` | record a review (clinician only) |
| GET | `/health` · `/ready` | liveness, readiness |

`POST /cases` accepts an optional `idempotency_key`: a retried submission returns the same case
instead of adjudicating it twice. Without one, every call creates a new case — correct for a
caller with no retry concern of its own.

A clinician's review vocabulary is `approve`, `deny`, `pend` — three values, not the machine's
two. A licensed clinician **may** issue an adverse determination; the machine may not. That
asymmetry is the reason `reviews.outcome` and `determinations.outcome` are different closed sets
rather than a shared one.

## Configuration

| variable | purpose |
| --- | --- |
| `DATABASE_URL` · `REDIS_URL` | storage and queue |
| `POLICY_URL` · `MEMBER_URL` | upstreams, both probed at startup |
| `LLM_PROVIDER` | `ollama` \| `gemini` \| `groq` — a closed set; a typo fails at startup |
| `LLM_URL` · `LLM_MODEL` | the endpoint and model name |
| `GROQ_API_KEY` / `GEMINI_API_KEY` | required only for the provider selected |
| `MIN_CONFIDENCE` | the gate's confidence floor |
| `PROBE_LLM_ON_STARTUP` | on by default: refuse to start if the model cannot honour a schema |

The startup probe is the point. A model that cannot produce schema-constrained output must fail
at boot, not mid-case where the resulting escalation looks like a fact about the member.

## Running and testing

```bash
cd services/adjudication
uv run python ../../scripts/migrate.py "postgresql://pramana:pramana@localhost:5432/pramana_adjudication" migrations
uv run uvicorn adjudication.main:app --port 8002    # the API
uv run python -m adjudication.worker                # the worker
uv run pytest                                       # 428 tests
```

Tests run against real Postgres and real Redis; the worker's job *is* that integration, so a mock
of either would test something else. No test calls a live model.

## Caveats

- **Run exactly one worker.** It consumes its stream under a fixed consumer name and re-reads its
  own pending entries before new ones, so two workers sharing that name will each pick up the
  other's in-flight message and adjudicate every case twice — doubling token spend and racing two
  determinations at one case. A stray worker has no listening port; check
  `lsof -nP -iTCP:6379`.
- **The worker does not restart.** Compose declares no `restart:` policy, and the bookkeeping
  inside the failure handlers can itself raise on a database blip, which would end the process.
- **It fail-fast probes `policy` and `member` at startup** and exits if either is unreachable.
  Since `policy` takes ~30s to load its cross-encoder, this service cannot be started in the same
  moment as the rest of the stack.
- **Criteria extraction is not deterministic across attempts.** The same member and the same
  retrieved chunks have produced different criteria counts on different attempts. That matters
  for the ablation: both arms re-extract, so they may differ in more than the arithmetic.
