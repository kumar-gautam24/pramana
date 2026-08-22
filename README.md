# Pramana

**Adjudicates US prior-authorization requests against CMS coverage determinations. It approves
what it can prove, and it cannot deny anything.**

> Sanskrit *pramāṇa* (प्रमाण) — a valid means of knowledge; what counts as legitimate evidence
> for a claim.

A request arrives with a member id, a procedure code and a clinical narrative. Pramana finds the
coverage policy that governed on the date of service, decomposes it into checkable criteria,
verifies each one against that member's record, and either approves — citing the criterion and
the evidence — or hands the case to a clinician with the evidence already assembled.

The coverage policy is real: CMS NCD 240.4 and 240.4.1 (CPAP for obstructive sleep apnea),
fetched from the CMS Coverage API. The member population is synthetic. No real patient data is
used anywhere.

---

## The one design constraint everything else follows from

**There is no deny path.** Not behind a flag, not in a test helper; the branch does not exist.
The gate has two outcomes, `APPROVE` and `ESCALATE`, and that is enforced in the shared
vocabulary rather than by convention.

| failure | consequence |
| --- | --- |
| Wrongly approved | The payer covers something it perhaps should not — **money** |
| Wrongly escalated | A clinician reviews a case they need not have — **time** |
| Wrongly denied | A member does not receive care — **harm** |

Deleting the third row is what makes automating the first two defensible. It also means every
failure mode is a cost rather than a harm, which is why the eval harness measures in dollars and
clinician-minutes.

Seven US federal and state requirements shaped the rest of the architecture directly:

| requirement | what it forces |
| --- | --- |
| CA SB 1120 — AI may not replace physician decision-making | no deny path |
| Medicare Advantage — no algorithm ignoring individual circumstances | per-member reasoning only |
| Illinois — only a clinical peer may issue an adverse determination | role gating at the gateway |
| Texas — the commissioner may audit the decision system at any time | append-only event log |
| Utah — AI use must be disclosed | disclosure in API and console |
| California — tools periodically assessed for accuracy | the eval harness is a legal obligation |
| Alabama — determinations rest on the enrollee's own history | no aggregate reasoning |

## Architecture

Seven services and a console. **Database per service** — no service reads another's tables. The
only shared code is `packages/common`, holding the criterion vocabulary and the gate: the two
things that must mean the same everywhere.

| component | port | database | responsibility |
| --- | --- | --- | --- |
| [`gateway`](services/gateway) | 8000 | none | the only public surface: identity, authorisation, rate limits, circuit breaking |
| [`policy`](services/policy) | 8001 | `pramana_policy` | CMS corpus ingest, chunking, hybrid retrieval, reranking, effective-dating |
| [`adjudication`](services/adjudication) | 8002 | `pramana_adjudication` | the pipeline, criteria, verifiers, determinations, audit log |
| [`evals`](services/evals) | 8003 | `pramana_evals` | golden cases, eval runs, cost scoring, the arithmetic ablation |
| [`auth`](services/auth) | 8004 | `pramana_auth` | users, argon2id passwords, sessions, roles |
| [`member`](services/member) | 8005 | `pramana_member` | the synthetic population and its clinical facts |
| [`web`](apps/web) | 3000 | none | the reviewer console (Next.js) |
| [`packages/common`](packages/common) | — | — | criterion types, gate, wire schemas |

Plus `adjudication-worker` — the only process that runs the pipeline — Postgres with pgvector,
and Redis.

Python 3.12, FastAPI, **raw SQL over asyncpg**: in a system an insurance commissioner may audit,
"show me the query that decided this" needs a literal answer. The console knows the gateway's
address and no other backend address, so a browser has no second door to find.

### How a request flows

```
browser ──► gateway ──► adjudication  POST /cases
            (auth,      (persist, enqueue, return 202)
             role,              │
             limits)            └──► redis stream ──► adjudication-worker
                                                            │
                                     ┌──────────────────────┼──────────────────────┐
                                     ▼                      ▼                      ▼
                                  member                 policy                  model
                              (facts: AHI,          (governing policy       (extract criteria,
                               study type,           on date of service,     judge narrative
                               adherence,            reranked chunks)        criteria)
                               notes)
                                     └──────────────────────┼──────────────────────┘
                                                            ▼
                                                     gate ──► determination
                                                            │
                                     case_events ──► redis pub/sub ──► SSE ──► browser
```

Submission is asynchronous: `POST /cases` persists the case, pushes its id onto a Redis stream
and returns `202` with the id. The worker consumes one case at a time — deliberately, because
each case costs several model calls against a rate-limited provider, and a worker that
saturated the limit would measure the token budget rather than the system.

### How a case is decided

Six stages, each appending exactly one row to the audit log:

```
started ─► eligibility ─► policy ─► criteria ─► criterion (one per criterion) ─► decision
```

1. **eligibility** — `member` is asked whether coverage was active on the date of service. No
   record and inactive record are treated differently: the first is a missing document, the
   second a contradicting one.
2. **policy** — `policy` resolves which version of which NCD was in force on that date, then
   retrieves the passages that matter. The query is built from the submitted narrative, falling
   back to the codes.
3. **criteria** — the model reads those passages and emits criteria in **disjunctive normal
   form**: several alternative sets, any one of which satisfies the policy. NCD 240.4 really is
   a disjunction — AHI ≥ 15 with a valid study, *or* AHI 5–14 with documented symptoms — so a
   single conjunction would misread it. Each criterion is typed and carries validated
   parameters plus the id of the chunk it came from.
4. **criterion** — each is verified according to its type:

   | type | verified by | example |
   | --- | --- | --- |
   | `threshold` | SQL fetch, Python comparison | AHI ≥ 15 |
   | `enum` | SQL fetch, Python membership | study type is one of four |
   | `temporal` | SQL fetch, Python date arithmetic | study within 365 days |
   | `judgment` | retrieval + model, with a grounded span | "symptoms are documented" |

5. **decision** — the gate runs once per criteria set. A set approves only if every criterion is
   `met` and every confidence clears the threshold. On escalation the system reports the
   blocking criteria of the **closest** set — fewest unmet — because a reviewer needs to know
   which single document would have settled the case.

**The model decides what the rules are; deterministic code checks the facts they point at.**
Only `judgment` criteria reach the model at verification time. No threshold, membership or date
comparison is ever left to it — that claim is the project's thesis, and there is a run mode that
ablates it specifically so it can be tested rather than asserted.

### Retrieval

The corpus is two NCDs, but the mechanics generalise. Ingest fetches JSON from the CMS Coverage
API, unescapes the doubly-escaped HTML, and chunks **heading-aware** — the headings are
`<strong>` tags rather than `<h1>`–`<h6>`, so a generic chunker finds none and silently produces
one enormous chunk.

Search is hybrid: vector similarity (`BAAI/bge-small-en-v1.5`) fused with lexical matching, then
reranked by a cross-encoder (`Xenova/ms-marco-MiniLM-L-6-v2`). The reranker score is an
**unbounded logit**, not a probability — observed between 0.56 and 7.00 on this corpus — so any
threshold has to be calibrated to that scale.

Effective-dating is resolved *before* retrieval, against the full policy table: a search dated
2026 must not return text that a 2009 revision superseded. Re-deriving the governing version
after retrieval reintroduces a bug this project has already shipped twice.

### The audit log

Every stage appends to `case_events` — append-only, one row per stage, ordered by a per-case
sequence — and publishes the same row to Redis pub/sub. The console's live view subscribes to
that channel over SSE. **The audit trail a regulator inspects and the live view a reviewer
watches are the same data**, which is what stops them drifting.

The `decision` event is the one exception to append-then-act: it describes the outcome of the
transaction that persists it, so it is written only after that transaction commits. Otherwise a
failed commit would leave a log permanently claiming a case was decided when no determination
exists.

Determinations and their criterion results are written in a single transaction — a reviewer must
never see a determination with no evidence behind it, or evidence with no determination. A
UNIQUE constraint on `determinations.case_id` makes "one case, one determination" a database
fact rather than an application convention.

### Reliability

- **Transient upstream failures** — a 429, a 5xx, a timeout — are retried by the *worker* with a
  bounded backoff ladder, and **every attempt is appended to the audit log**. The retry lives in
  the worker rather than in an HTTP client because a client can wait, but only the worker can say
  so in the record. Before this existed, a rate limit on our side became a permanent escalation
  on a clinician's queue: a fact about our infrastructure delivered as though it were a fact
  about the member.
- **Permanent failures** — a 4xx, an unparseable body — are never retried. Schema drift does not
  heal on a second attempt, and the ladder spent on it is time a reviewer waits for an answer
  that was already decided.
- **The queue is at-least-once.** A case may legitimately be delivered twice, so the pipeline is
  idempotent: a case that already holds a determination returns it rather than re-deriving it.
- **The gateway breaks the circuit** after 5 consecutive failures to an upstream, for 30 seconds.
- **Migrations are forward-only and refuse rather than guess.** One that would rewrite a recorded
  determination stops with the offending rows named and asks a human to resolve them.

Every decision a reader might second-guess is in [`docs/decisions`](docs/decisions/) — 21 ADRs.

## Try it

You need Docker and a language model the adjudication service can reach.

```bash
git clone https://github.com/kumar-gautam24/pramana.git
cd pramana
cp .env.example .env
```

Pick a model — `.env` is the only place that choice lives. The default is
[Ollama](https://ollama.com) on the host so it can use your GPU
(`ollama pull qwen2.5:14b-instruct`); or uncomment the Groq or Gemini block and add a key.

**The stack does not come up in one command**, and that is a real defect rather than a gap in
these instructions: `policy` spends ~30s loading a cross-encoder on first boot, `adjudication`
probes `policy` at startup and exits if it cannot reach it, and nothing declares a `restart:`
policy.

```bash
docker compose up -d
until curl -sf localhost:8001/ready; do sleep 5; done   # wait for the cross-encoder
docker compose up -d adjudication
sleep 10
docker compose up -d evals gateway web
docker compose ps -a                                    # expect 10 services, all Up
```

`auth` and `evals` migrate themselves at startup; the other three do not, so that a schema
change is a step an operator takes rather than something that happens on container restart:

```bash
for s in policy member adjudication; do
  (cd services/$s && uv run python ../../scripts/migrate.py \
      "postgresql://pramana:pramana@localhost:5432/pramana_$s" migrations)
done
```

Load the corpus, the population, and an account:

```bash
curl -sX POST localhost:8001/ingest -H 'content-type: application/json' -d '{"ncd_id": 226}'
curl -sX POST localhost:8001/ingest -H 'content-type: application/json' -d '{"ncd_id": 330}'
curl -sX POST localhost:8005/seed   -H 'content-type: application/json' -d '{"seed": 42}'
curl -sX POST localhost:8004/users  -H 'content-type: application/json' \
  -d '{"email":"operator@example.com","password":"choose-something","role":"admin"}'
```

Then open **<http://localhost:3000>**, sign in, and submit a case from `/cases/new`. Roles are
`clinician`, `reviewer`, `operator`, `admin`; only a clinician may record a review, only an
operator or admin may reach the eval screens.

The five seeded members exercise different paths: `p1` has an AHI of 46.9 with a valid study,
`p2` sits at 14.446 and is the deliberate near-miss, `p5`'s study is one channel short of
qualifying. A case settles in roughly twenty seconds against an unthrottled model.

## Status and results

Everything in the design is built and runs. A case submitted through the gateway is adjudicated
against the real text of NCD 240.4 by a real model and decided with a complete audit trail.

**No number appears here until a run produced it.** What has been established by running it:

- **Criteria extraction works on unseen policy.** Given NCD 240.4's retrieved text the model
  returns the rule in correct disjunctive normal form. Every criterion passes parameter
  validation; every citation resolves to a chunk that was genuinely retrieved.
- **The near-miss case behaves.** AHI 14.446 fails `>= 15` in one set and `<= 14` in the other;
  the system escalates with `criterion_not_met` and names the AHI criterion. Both comparisons in
  Python.
- **Retrieval needs the narrative, not the codes.** A codes-only query (`E0601 G47.33`) reached
  one of the five criteria-bearing chunks, reranker scores flat around −11 — a cross-encoder
  saying *nothing here matches*. The same search with a narrative reached four, scores spread
  from 7.2 down.
- **Ingest conserves content.** 469 source tokens in, 469 stored, zero missing.

**The arithmetic ablation has run once and produced no delta.** Both arms on the same commit and
model over the same five cases:

| | baseline | ablated (`model_arithmetic`) |
| --- | --- | --- |
| reached the gate | 4 of 5 | **0 of 5** |
| short-circuited on an unreachable model | 1 | **5** |

The case-level delta was zero on every metric, and **that zero is an artifact**: the ablated arm
sends a model call per deterministic comparison, which exhausted its retry ladder against an
8,000 tokens-per-minute provider ceiling before deciding anything. A run that adjudicated
nothing scored identically to one that adjudicated most of the set. The delta stays unmeasured
and no figure for it appears here or in ADR-0003.

**Not yet measured:** auto-approval rate (no case has approved — see below), verdict accuracy,
citation correctness, the threshold sweep and therefore the operating point, and the ablation
delta. Extraction F1 is computed and currently returns 0.0 on every case with zero matches,
which means the metric reports nothing usable rather than that extraction failed.

The golden set is **five cases against a design target of sixty**, of which at least twenty must
escalate and at least eight be near-miss. Labels are human-authored by rule — a model grading a
model measures agreement, not correctness — so that is the ceiling on everything the harness can
currently say.

## Known limits

- **No case has ever approved, and the gap is one criterion wide.** The model correctly extracts
  NCD 240.4's *procedural* requirements alongside its clinical ones — that the study was ordered
  by the treating physician, that the supplier educated the beneficiary. Those are genuinely in
  the policy; the synthetic charts document the first (confirmed met at 0.95) and not the second.
  Adding CPAP-education text to the generator is a one-line change, but it decides whether a
  human-authored `approve` label is reachable, so it is a question about the label rather than a
  fixture tweak.
- **The console has never been rendered by anyone.** It compiles, typechecks and has 58 unit
  tests; no human or browser has looked at a screen. Treat its screens as unverified.
- **The ablation cannot run on a rate-limited free tier.** Use a provider with real headroom, or
  expect no result. The gateway also permits two eval runs an hour.
- **The comparison endpoint will certify a pair where one arm adjudicated nothing.** It checks
  that two runs share a commit, model and prompt version and differ only in their ablation — not
  that either produced a determination. Known and unfixed.
- **`POST /users` on `auth` is unauthenticated and port 8004 is published**, so anything that can
  reach the host port can mint an admin. Fine on a laptop; wrong the moment this compose file is
  read as the shape of a deployment.
- **`evals`, `auth` and `gateway` have no test suites.** `adjudication` has 428, `policy` 93,
  `member` 91, `packages/common` 45, `apps/web` 58.
- **Nothing restarts.** No `restart:` policy anywhere, and a database blip during the worker's
  bookkeeping can end the process.
- **Run exactly one worker.** It consumes its stream under a fixed consumer name; two workers
  sharing it will each pick up the other's in-flight messages and adjudicate every case twice.
  A stray worker has no listening port, so check the connection it holds: `lsof -nP -iTCP:6379`.

## Development

Each service is independently deployable and shares nothing but `packages/common`. Importing
across service boundaries is the invariant being violated, not a convenience being discovered.

```bash
cd services/adjudication && uv run pytest      # 428
cd services/policy       && uv run pytest      #  93
cd services/member       && uv run pytest      #  91
cd packages/common       && uv run pytest      #  45
cd apps/web && npm install && npm test         #  58
ruff check .                                   # the lint gate
```

Tests run against real Postgres and real Redis rather than mocks, because what is worth testing
here is integration — a stream, a transaction, a schema, a population-level invariant. They
never call a live model: every model-backed test stubs the provider, so the suite runs on a
machine with no GPU and no network.

`ruff format` is deliberately not used; line breaks are chosen by hand to keep comments readable.

## Lineage

The gateway, service skeleton, job queue, confidence gate and eval harness are carried from
[Deflect](https://github.com/kumar-gautam24/deflect), which answered questions from FastAPI's
documentation with citations, or refused to. Two things changed. Deflect's refusal set was
entirely out-of-domain, which made refusing look easier than it is — hence the near-miss
requirement here. And its escalations went nowhere, because no human was on the other end; here
they go to a clinician, and that clinician's decision becomes the next eval case.

## Licence note

The corpus is CMS Medicare Coverage Database data — public domain, but the bulk download requires
accepting ADA/AMA/NUBC terms because CPT descriptions are AMA-copyrighted. **CPT descriptions are
never committed to this repository**; codes are ingested as identifiers only. The corpus is
gitignored and reproduced by the `/ingest` calls above, so a clone fetches its own copy under its
own acceptance of the CMS terms.

No real patient data is used anywhere. Members are generated with Synthea.
