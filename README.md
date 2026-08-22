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
| `evals` | golden cases, eval runs, two-level scoring, the model-arithmetic ablation |
| `apps/web` | the console: intake, queue, case detail, live step view, review submission, the eval harness |

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

The design numbered a `normalize` stage first, turning free text into a CPT code and an
ICD-10 code. It is **struck**, not deferred — see
[ADR-0018](docs/decisions/0018-no-normalize-stage.md). A prior-authorization request arrives
with its codes already assigned by the submitter's billing system, and asking a model to
produce them instead would put a model-generated fact at the one point in the pipeline nothing
downstream re-checks. The narrative a submission does carry is retrieval input: measured on
this corpus, a search built from the codes alone reaches one of the five passages that decide
these cases, and the same search with a narrative reaches four.

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

**There is no cost figure, and the one this section used to carry has been withdrawn.**

It read `$0` wrongly auto-approved and `$72` of clinician time wrongly escalated, over three
golden cases. Two of those three never reached the gate: they exhausted the retry ladder
against the model provider's rate limit and were short-circuited `upstream_unavailable`. The
harness scored them as escalations anyway, so one of the two $36 charges making up that $72 was
a rate limit priced as a clinical judgment. The bug is fixed — `evals.runner` now records an
unreachable upstream as unfinished, which is what that module's own docstring always said it
would do — and the number is withdrawn rather than restated, because a corrected figure over
the one remaining case is not a figure.

**The signature ablation has now been run, and it did not produce a delta.** Both arms, same
commit (`f82fb4a`), same model (`openai/gpt-oss-120b`), same five golden cases, 2026-08-22:

| | baseline (`none`) | ablated (`model_arithmetic`) |
| --- | --- | --- |
| reached the gate | 4 of 5 | **0 of 5** |
| short-circuited `upstream_unavailable` | 1 | **5** |
| comparison criteria evaluated | 49 | 21 (17 by the model) |
| wall clock | 372 s | 610 s |

The case-level delta was **exactly zero on every metric**, with no disagreements, and the pair
was certified `comparable`. That zero is an artifact and is reported here as one. The ablated
arm sends a model call for every deterministic criterion instead of comparing in Python, which
against this provider's ceiling of 8,000 tokens per minute exhausted the retry ladder on all
five cases; it adjudicated nothing. Scored as escalations — which is what the harness did until
today — that run reported figures identical to the baseline's, so a run that decided nothing
read as perfect agreement with one that decided four fifths of the set.

**The measured result is therefore about the apparatus, not about the thesis.** Three
conditions ADR-0003's experiment needs are now known to be unmet, and every one of them was
invisible to a green build: the harness could price an outage as a determination; the
comparison endpoint certifies that two runs share a commit, model and prompt version and differ
only in their ablation, but not that either arm produced an adjudication; and the ablated arm
cannot finish a single case on an 8,000 TPM key. The delta stays unmeasured, and no number for
it appears here or in [ADR-0003](docs/decisions/0003-ai-extracts-rules-code-checks-facts.md)
until a run produces one from two arms that both reached the gate.

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
- **Extraction precision and recall** against a human-authored criteria list. The harness does
  compute an F1 per case, and on 2026-08-22 it returned **0.0 on all five cases in both arms**,
  with `matched_count: 0` every time — the extractor's criteria never matched a single one of
  the human-authored `expected_criteria` strings. Whether that is a real disagreement about what
  NCD 240.4 requires or a matching rule comparing prose to prose is itself unmeasured, so the
  metric currently reports nothing usable and is listed here rather than above.
- **Verdict accuracy and citation correctness** per criterion.
- **The threshold sweep**, and therefore the chosen operating point.
- **The signature ablation's delta.** The pair has been run (see *Measured*); the ablated arm
  reached the gate on none of its five cases, so there is no delta and there must not be a
  number here until two arms both finish.
- **The golden set is five cases** against a design target of sixty, of which at least twenty
  must escalate and at least eight be near-miss ([ADR-0009](docs/decisions/0009-near-miss-cases-required.md)).

### Known open

- **No case can currently approve, and the code is still not why — but the remaining gap is now
  exactly one criterion wide.** The model correctly extracts NCD 240.4's *procedural*
  requirements alongside its clinical ones — that the study was ordered by the treating
  physician, that the supplier educated the beneficiary. Those are genuinely in the policy, and
  the generated member charts did not document them. The population was regenerated on
  2026-08-22 against a `member` image that writes a referring physician's order, and the
  adjudicator confirms it: on member p1 the order criterion now returns **met at confidence
  0.95**. What blocks that case is a single remaining criterion — *"the CPAP provider performed
  education of the beneficiary on proper device use prior to therapy"* — which the generator
  models nowhere, exactly as it modelled no physician's order six days earlier. Adding it is a
  generator change, but it decides whether golden case 2's human `approve` label is reachable,
  so it is a question about the label and not a fixture tweak to be made quietly.
- **The eval harness has never been pointed at a golden set worth the name.** Three cases,
  against a target of sixty. Every number the harness can produce is therefore a demonstration
  that the measurement exists rather than a measurement. Labels are human-authored by rule
  ([ADR-0008](docs/decisions/0008-human-authored-golden-labels.md)), so this is human work and
  cannot be generated.
- **Nothing added on 2026-08-22 has executed.** The console compiles and its read routes were
  exercised live during the gateway work, but no screen has been rendered against a running
  stack; `adjudication`'s migrations `0004` and `0005` have not been applied to any database;
  no case has run in the ablated mode; and no retry ladder has waited out a real 429. The
  compose images are wired but not built.

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

A transient upstream failure — a 429, a 5xx, a timeout — is retried by the worker with
backoff, and **each attempt is appended to that log**
([ADR-0020](docs/decisions/0020-retry-transient-upstream-failures-in-the-worker.md)). The
retry is in the worker rather than in a client for exactly that reason: a client can wait, but
only the worker can say so in the record. Before it existed, a rate limit on our side became a
permanent escalation on a clinician's queue — a fact about our infrastructure delivered as
though it were a fact about the member.

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
