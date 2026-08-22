# Pramana

**Adjudicates US prior-authorization requests against CMS coverage determinations. It
approves what it can prove, and it cannot deny anything.**

> Sanskrit *pramāṇa* (प्रमाण) — a valid means of knowledge; what counts as legitimate evidence
> for a claim. The system asserts nothing it lacks pramāṇa for.

---

**Status: it runs end to end, on real inputs, and the numbers it has earned are small.**

All seven components of the design exist:

| component | what is built |
| --- | --- |
| `policy` | CMS corpus ingest, chunking, pgvector retrieval, cross-encoder reranking, effective-dating |
| `member` | Synthea-derived population, eligibility, sleep studies, adherence, conditions, narrative notes |
| `adjudication` | the pipeline, its worker, criteria extraction, the four verifiers, the gate, the append-only event log, SSE |
| `auth` | accounts, argon2id passwords, sessions, roles |
| `gateway` | the single front door: route table, session resolution, role gating, rate limits, circuit breaker |
| `evals` | golden cases, eval runs, two-level scoring, ablations |
| `apps/web` | the reviewer console: queue, case detail, live step view, review submission |

plus `packages/common` (the shared vocabulary and the gate) and the migration runner.

A case submitted through the gateway is adjudicated against the real text of CMS NCD 240.4 by
a real model and decided with a complete audit trail. That has been run. What it produced —
including the one thing that currently stops any case approving — is under
[What is measured](#what-is-measured-and-what-is-not), and nothing there is estimated.

## The problem

A prior-authorization request can be approved, denied, or sent to a human. Denials are where
the harm sits — algorithmic mass-denial is why California passed SB 1120, the *Physicians Make
Decisions Act*.

Most requests are routine approvals that consume clinical staff hours. The hard ones need a
clinician regardless. So Pramana clears the routine ones — each citing the exact policy
criterion and the exact line in the member's record satisfying it — and hands everything else
to a clinician with the evidence already assembled.

**It has no deny path.** Not behind a flag; the branch does not exist. That bounds every
failure mode:

| failure | consequence |
| --- | --- |
| Wrongly approved | The payer covers something it perhaps should not — **money** |
| Wrongly escalated | A clinician reviews a case they need not have — **time** |
| Wrongly denied | A member does not receive care — **harm** |

Deleting the third row is what makes automating the first two defensible.

## The regulation is the specification

This is not compliance theatre bolted onto a demo. Seven requirements across US federal and
state law dictate the architecture:

| requirement | what it forces |
| --- | --- |
| CA SB 1120 — AI may not replace physician decision-making | no deny path |
| Medicare Advantage — no algorithm ignoring individual circumstances | per-member reasoning |
| Illinois — only a clinical peer may issue an adverse determination | role gating |
| Texas — the commissioner may audit the decision system at any time | append-only event log |
| Utah — AI use must be disclosed | disclosure in API and console |
| California — tools periodically assessed for accuracy | the eval harness is a legal obligation |
| Alabama — determinations rest on the enrollee's own history | no aggregate reasoning |

## How it decides

A coverage policy is not a passage — it is a checklist. So the pipeline decomposes and
verifies rather than retrieving and answering:

```
eligibility ─► governing policy ─► decompose into criteria
                                                        │
                          ┌─────────────────────────────┴──────────────────┐
                          │  verify each criterion, in parallel            │
                          │    threshold / enum / temporal  → SQL          │
                          │    judgment                     → retrieval+LLM│
                          └─────────────────────────────┬──────────────────┘
                                                        ▼
                              all met & confident ─────► APPROVE
                              any not met ─────────────► ESCALATE
                              any insufficient ────────► ESCALATE
```

The design numbers a `normalize` stage first, turning free text into a CPT code and an
ICD-10 code. It is **not built**: a case arrives carrying its codes, and its submitted
narrative is what the policy search runs on. Whoever builds intake either adds that stage or
strikes it from the spec.

**The model decides what the rules are. Deterministic code checks the facts they point at.**
It extracts criteria from policy prose it has never seen, classifies each by type, and emits
parameters — then SQL performs the comparison. Nothing is hardcoded per policy, and no date
arithmetic is left to a language model.

Refusal is explainable per criterion, not per query:

> Escalated — criterion 2 (documented moderate or severe OSA) has insufficient evidence in the
> record. Criteria 1, 3, 4 met.

## What is measured, and what is not

### How it is scored

Two levels. At the criterion level: extraction precision and recall against a human-authored
list, verdict accuracy, citation correctness. At the case level, the numbers that carry
units:

- **wrongly auto-approved** × average claim amount
- **wrongly escalated** × review minutes × loaded clinician rate

The operating point is chosen by plotting total cost against the confidence threshold and
taking the minimum — an argument rather than a preference.

Golden-case labels are human-authored. A model grading a model measures agreement, not
correctness. At least 8 of the cases are *near-miss* — in-domain and partially satisfied —
because a refusal set made only of obviously out-of-scope requests measures the easy half of
the problem.

### Measured

One eval run has been scored, over **three golden cases**:

| | |
| --- | --- |
| wrongly auto-approved | **$0** |
| wrongly escalated | **$72** of clinician time |

That is the entire cost result this project has. Three cases is not a sample; it is a
demonstration that the number exists and has units.

Three qualitative results, each from a live run against the real corpus, real member records
and a real model:

- **Criteria extraction works on policy it has never seen.** Given NCD 240.4's retrieved text,
  the model returned the rule in disjunctive normal form — one set requiring AHI ≥ 15 with a
  valid study type, another requiring AHI 5–14 *with* documented symptoms — which is what the
  policy actually says. Every criterion passed parameter validation; every citation resolved to
  a chunk that had genuinely been retrieved. This is [ADR-0011](docs/decisions/0011-alternative-criteria-sets.md)
  on real input rather than on a fixture.
- **The near-miss case behaves.** A member with AHI 14.446 fails `>= 15` in the first set and
  fails `<= 14` in the second. The system escalated with `criterion_not_met` and named the AHI
  criterion. Both comparisons were done in Python, not by the model — the whole argument of
  [ADR-0003](docs/decisions/0003-ai-extracts-rules-code-checks-facts.md), end to end on real data.
- **Retrieval needs the narrative, not the codes.** For NCD 240.4, the criteria-bearing chunks
  are 57, 58, 59, 69 and 70. A query built from a case's codes alone (`E0601 G47.33`) returned
  exactly one of them and filled the rest with boilerplate, its reranker scores flat in a band
  around −11 — a cross-encoder saying *nothing here matches*. A natural-language query returned
  57, 58, 59 and 70, with scores spread from 7.2 down. Codes are out of distribution for a model
  trained on question/passage pairs. A case now carries its submitted narrative and falls back
  to its codes.

### Not measured

- **Auto-approval rate.** No case has approved end to end yet, and the reason is data, not
  logic — see below.
- **Extraction precision and recall** against a human-authored criteria list. The extraction
  above was read and judged correct by a person; it was not scored.
- **Verdict accuracy and citation correctness** per criterion.
- **The threshold sweep**, and therefore the chosen operating point.
- **The signature ablation** — the run that has the model do the arithmetic instead of SQL, to
  prove invariant 2 empirically. It returns 501 deliberately: adjudication has no such run mode
  yet, and a fabricated number would defeat the point of the experiment.
- **The golden set is three cases** against a design target of sixty, of which at least twenty
  must escalate and at least eight be near-miss ([ADR-0009](docs/decisions/0009-near-miss-cases-required.md)).

### Known open

- **No case can currently approve, and the code is not why.** The model correctly extracts NCD
  240.4's *procedural* requirements alongside its clinical ones — that the study was ordered by
  the treating physician, that the supplier educated the beneficiary. Those are genuinely in the
  policy, and the generated member charts do not document them, so those criteria return
  `insufficient_evidence` and the gate correctly refuses to approve. The generator now writes a
  referring physician's order into the record; the seeded population has not been regenerated
  against it.
- **A rate limit can still land on a clinician's queue.** A 429 from the model provider becomes
  `UpstreamUnavailable`, which becomes an escalation — a fact about our infrastructure arriving
  as though it were a fact about the member. Batching a case's judgment criteria into one call
  ([ADR-0015](docs/decisions/0015-batched-judgment-verification.md)) took a case from about
  seven model calls to two and made this rare rather than routine; the durable fix is a worker
  that retries with backoff and records each attempt in the event log, so the audit claim stays
  honest.
- **What a clinician may record as their own outcome is not yet a closed vocabulary.**
  `reviews.outcome` is deliberately unconstrained in the database until the regulatory question
  is settled; the console constrains it at the point of authoring.

## Architecture

Seven services: `gateway` (8000, no database), `policy` (8001), `adjudication` (8002),
`evals` (8003), `auth` (8004), `member` (8005), and a Next.js reviewer console (3000).
Database per service, Postgres with pgvector, Redis. Python 3.12, FastAPI, raw SQL over
asyncpg — in a system an insurance commissioner may audit, "show me the query that decided
this" needs a literal answer ([ADR-0013](docs/decisions/0013-raw-sql-not-orm.md)).

The model is a configuration choice: Ollama locally, or any hosted provider that will carry
the extraction schema **unmodified** ([ADR-0014](docs/decisions/0014-one-schema-three-providers.md)).
The console holds the gateway's address and no other backend address, so there is no second
door for a browser to find
([ADR-0017](docs/decisions/0017-gateway-establishes-identity.md)).

Each pipeline stage appends to an append-only `case_events` log and publishes to Redis, so
the audit trail regulators inspect and the live step view reviewers watch are the same data.

Every decision that a reader might second-guess is recorded in
[`docs/decisions`](docs/decisions/).

## Lineage

The gateway, service skeleton, job queue, confidence gate and eval harness are carried over
from [Deflect](https://github.com/kumar-gautam24/deflect), an earlier project answering
questions from FastAPI's documentation with citations, or refusing to.

What changed: Deflect's refusal set was entirely out-of-domain, which made refusing look
easier than it is — [ADR-0009](docs/decisions/0009-near-miss-cases-required.md) records how
that was found and what it cost. Its escalations also went nowhere, because no human was on
the other end. Here they go to a clinician, and that clinician's decision becomes the next
eval case.

## Licence note

The corpus is CMS Medicare Coverage Database data — public domain, but the bulk download
requires accepting ADA/AMA/NUBC terms because CPT descriptions are AMA-copyrighted. **CPT
descriptions are never committed to this repository**; codes are ingested as identifiers
only. The corpus directory is gitignored and reproduced by script, so a clone fetches its own
copy under its own acceptance of the CMS terms. See
[ADR-0004](docs/decisions/0004-cms-corpus-and-cpt-licensing.md).

No real patient data is used anywhere. Members are generated with Synthea.
