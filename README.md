# Pramana

**Adjudicates US prior-authorization requests against CMS coverage determinations. It
approves what it can prove, and it cannot deny anything.**

> Sanskrit *pramāṇa* (प्रमाण) — a valid means of knowledge; what counts as legitimate evidence
> for a claim. The system asserts nothing it lacks pramāṇa for.

---

**Status: in development.** The design is complete and approved
([`docs/specs`](docs/specs/2026-08-15-pramana-design.md)); the implementation has not started.
This README describes what is being built, and will be replaced with measured results as they
exist. No number appears here until a script produces it.

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
normalize ─► eligibility ─► governing policy ─► decompose into criteria
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

**The model decides what the rules are. Deterministic code checks the facts they point at.**
It extracts criteria from policy prose it has never seen, classifies each by type, and emits
parameters — then SQL performs the comparison. Nothing is hardcoded per policy, and no date
arithmetic is left to a language model.

Refusal is explainable per criterion, not per query:

> Escalated — criterion 2 (documented moderate or severe OSA) has insufficient evidence in the
> record. Criteria 1, 3, 4 met.

## What is measured

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

## Architecture

Seven services: `gateway` (8000, no database), `policy` (8001), `adjudication` (8002),
`evals` (8003), `auth` (8004), `member` (8005), and a Next.js reviewer console (3000).
Database per service, Postgres with pgvector, Redis. Python 3.12, FastAPI, local models via
Ollama.

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
