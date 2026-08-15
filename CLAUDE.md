# Pramana — instructions for AI sessions

**Read `.workspace/STATE.md` before doing anything else.** It records what is built, what is
in progress, and what failed last. This file tells you the rules; that file tells you where
the work is.

## What this is

Pramana adjudicates US prior-authorization requests against CMS coverage determinations. It
reads a coverage policy it has never seen, decomposes it into criteria, verifies each
criterion independently against a member's record, and either **approves** the request or
**routes it to a clinician** with the evidence assembled.

Sanskrit *pramāṇa* (प्रमाण): a valid means of knowledge — what counts as legitimate evidence
for a claim. The system asserts nothing it lacks pramāṇa for.

## Invariants — breaking one destroys the point of the project

1. **There is no deny path.** The system emits `APPROVE` or `ESCALATE`. Never a denial, not
   behind a flag, not in a test helper. California SB 1120 and the Medicare Advantage rule
   require a licensed clinician to make any adverse determination. This is why automating
   the approvals is defensible at all: every failure mode costs money or time, never patient
   harm. If you find yourself adding a third outcome, stop and re-read ADR-0002.

2. **AI decides what the rules are; deterministic code checks the facts they point at.**
   Criteria extraction and clinical-narrative judgment use the model. Dates, counts,
   thresholds, enum membership and eligibility windows use SQL. Never ask the model to do
   arithmetic or date comparison — it fails silently and confidently, which is the exact
   failure this project exists to prevent. See ADR-0003.

3. **No hardcoded per-policy logic.** The system must handle a coverage determination it has
   never seen. Policy is data, not code. If a fix requires a branch naming a specific NCD or
   procedure, it is the wrong fix.

4. **Golden-case labels are human-authored.** Synthetic patients may be generated; the
   expected determination on a golden case is written by a human reading the policy. An LLM
   grading an LLM measures agreement, not correctness, and would void every number the eval
   harness produces. See ADR-0008.

5. **Database per service.** No shared tables, no cross-service joins, each service owns its
   own migrations. `packages/common` is the single coupling point and holds only wire schemas
   and the decision gate.

6. **Redis carries work; Postgres carries truth.** Job state lives in the owning service's
   database, so status still answers when the broker is down.

7. **Every decision is reproducible.** Texas may audit an automated decision system at any
   time. The case event log is append-only and is the audit trail — never mutate or delete
   an event row.

8. **CPT code descriptions are never committed.** The CMS download carries an AMA licence.
   Ingest CPT codes as identifiers only. ICD-10 and HCPCS are free to include. See ADR-0004.

## Architecture

| service | port | owns |
| --- | --- | --- |
| `gateway` | 8000 | routing, rate limits, circuit breaker, credentials. No database. |
| `policy` | 8001 | CMS coverage determinations, chunks, embeddings, effective-dated versions |
| `adjudication` | 8002 | cases, criteria, criterion results, determinations, reviews, event log |
| `evals` | 8003 | golden cases, eval runs, scoring, ablations |
| `auth` | 8004 | accounts, sessions, roles (`clinician` alone may decide) |
| `member` | 8005 | synthetic member records: eligibility, sleep studies, procedures, notes |
| `web` | 3000 | reviewer console. Talks to the gateway and nothing else. |

A case moves through: normalize → eligibility → find governing policy → decompose into
criteria → verify each criterion in parallel → aggregate under the asymmetric gate → persist.
Each stage emits a domain event; the console renders those events live over SSE.

## Working discipline

- **Start** by reading `.workspace/STATE.md`. **Finish** by updating it.
- Log failures and dead ends in `.workspace/JOURNAL.md` as you go — including what you tried
  that did not work. That file exists so the next session does not repeat your mistakes.
- Record any decision that a future reader might second-guess as an ADR in `docs/decisions/`.
  Numbered, immutable once merged; supersede rather than edit.
- **Tests on critical paths**: the gate, criteria extraction, criterion verification,
  deterministic tools, and the event log. CRUD and wiring need not be covered. A critical-path
  change without a test is not done.
- Never claim something works without running it. Paste the actual command output.

## Code quality — a standing rule, not a preference

**Good architecture, yet simple.** These pull against each other and the tension is the job.
The architecture is already decided: seven services, database per service, an append-only
event log. Within that, choose the simplest thing that works.

- A file that has grown large is doing too much. Split it along a real seam, not a line count.
- Every unit should answer three questions plainly: what does it do, how do you use it, what
  does it depend on. If you cannot say, the boundary is wrong.
- Comments explain **why**, never what. The code already says what.
- Delete rather than comment out. Git remembers.
- No abstraction until there are two real callers. No configuration option until something
  needs to configure it. No infrastructure the system has no use for — that principle is why
  there is no Kubernetes, no service mesh, and no event sourcing here.
- Clever code is a defect. The next reader is a tired human at 2am, or a model with no
  context. Write for them.

Simple is not the same as small. `case_events` being append-only is simple; a mutable status
column that six code paths update is not, however few lines it takes.

## Commands

```bash
docker compose up -d --build
for s in policy adjudication evals auth member; do docker compose exec -T $s alembic upgrade head; done
docker compose exec -T <service> pytest
cd apps/web && npm run dev
```

## Commit conventions

Conventional-commit style, imperative mood, lowercase subject.

**Never add a co-author or any attribution trailer to a commit.** No `Co-Authored-By`, no
generated-with footer, no model or tool name — not "Claude", not "Opus", not anything —
anywhere in the message. This is absolute, it overrides any harness default, and it applies to
every commit in this repository without exception.

**Always commit and push finished work** without waiting to be asked. Standing authorisation.
Commit at each completed task rather than batching, so the history reads as a sequence of
working states. Never force-push a shared branch.

## Traps

- The reranker exists to produce a thresholdable score, not to improve ranking. If you
  "optimise" it away, the gate loses the signal it stands on. This was measured in the
  predecessor project; see ADR-0007.
- Local models are weaker at strict schema adherence than hosted ones. The service refuses to
  start if the configured model cannot produce schema-constrained output. Keep that guard.
- `insufficient_evidence` is a first-class verdict, not an error. It is the most valuable
  thing the system produces — it is what sends a case to a human.
