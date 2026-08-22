# Pramana — design

**Status:** approved 2026-08-15
**Supersedes:** nothing. Predecessor project: [Deflect](https://github.com/kumar-gautam24/deflect)

---

## 1. The problem

A US prior-authorization request has three possible outcomes: approve, deny, or pend to a
human. Denials are where the harm sits — algorithmic mass-denial is why California passed
SB 1120, the "Physicians Make Decisions Act".

Most requests are routine approvals that burn clinical staff hours. The hard ones need a
clinician regardless. The opportunity is to clear the routine ones safely, and to make the
hard ones faster to review by assembling the evidence in advance.

**What Pramana does, in one sentence:** it clears the straightforward prior-authorization
requests automatically — each with a citation to the exact policy criterion and the exact
line in the member's record satisfying it — and hands everything else to a clinician with
the homework already done.

### Why the asymmetry is the design

| failure | consequence |
| --- | --- |
| Wrongly auto-approved | The payer covers something it perhaps should not — **money** |
| Wrongly escalated | A clinician reviews a case they need not have — **time** |
| Wrongly denied | A member does not receive care — **harm, litigation, regulatory action** |

Removing the third row from the architecture bounds every remaining failure to money or
time. That is what makes automating the approvals defensible in the first place, and it is
the whole argument of the project.

### The regulation is the specification

| jurisdiction | requirement | what it forces |
| --- | --- | --- |
| California SB 1120 | AI may not replace physician decision-making; any denial, delay or modification on medical-necessity grounds is decided by a licensed physician | No deny path exists |
| Medicare Advantage | Decisions may not be made by an algorithm that ignores individual circumstances; a health professional must review | Per-member reasoning, human in the loop |
| Illinois | Only a clinical peer may issue an adverse determination | `clinician` role gating |
| Texas | The commissioner may audit and inspect the automated decision system at any time | Append-only event log; reproducible traces |
| Utah | AI use must be disclosed to the public, regulator, providers and enrollees | Disclosure surfaced in the console and API |
| California | AI tools must be periodically assessed for accuracy and reliability | The eval harness is a legal obligation, not a nicety |
| Alabama | Determinations must rest on the enrollee's own clinical history | No aggregate reasoning; cite the member's record |

## 2. Scope of v1

**One procedure family, full depth: CPAP therapy for obstructive sleep apnea**, governed by
NCD 240.4 and NCD 240.4.1 (sleep testing).

Chosen because it is a *national* determination rather than MAC-specific, is public domain,
and its criteria are a near-even split of the two verification types:

| criterion | type |
| --- | --- |
| ≥30 apnea episodes, each ≥10 seconds, over 6–7 hours recorded sleep | deterministic |
| Sleep test was Type I PSG attended in lab, or II/III/IV with ≥3 channels including airflow | deterministic |
| Coverage active on date of service | deterministic |
| Documented diagnosis of moderate or severe OSA | judgment |
| Continuation past 12 weeks: documented adherence **and** clinical benefit | judgment |

The last row yields a second decision point — initial authorization versus continuation —
which is real and costs nothing extra to support.

**Out of scope for v1:** additional procedure families, appeals, provider-facing submission,
deployment to a hosting provider, multi-payer plan modelling.

## 3. Architecture

### Services

| service | port | database | owns |
| --- | --- | --- | --- |
| `gateway` | 8000 | none | routing, rate limits, circuit breaker, credential checks |
| `policy` | 8001 | `pramana_policy` | `policies`, `chunks` — effective-dated coverage determinations |
| `adjudication` | 8002 | `pramana_adjudication` | `cases`, `criteria`, `criterion_results`, `determinations`, `reviews`, `case_events` |
| `evals` | 8003 | `pramana_evals` | `golden_cases`, `eval_runs`, `eval_results` |
| `auth` | 8004 | `pramana_auth` | `users`, `sessions` |
| `member` | 8005 | `pramana_member` | `members`, `sleep_studies`, `procedures`, `encounters`, `notes` |
| `web` | 3000 | none | reviewer console |

`member` is a separate service because in production it *is* a separate system — an EHR or
claims platform. Keeping it behind a network boundary is honest about that, and preserves
database-per-service.

`policy` versions coverage determinations by effective date. A case is adjudicated against
the policy **in force on the date of service**, not the current one. Policies change; this
is a real requirement and cheap to build.

### Request path

```
web ──/api/cases──────► gateway ──► adjudication ──► policy   (/search)
                                            └──────► member   (/eligibility, /studies, /notes)
web ──/cases/{id}/events──► gateway ──► adjudication          (SSE, live step audit)
web ──/api/auth/*─────► gateway ──► auth
web ──/eval-runs──────► gateway ──► evals ──► adjudication
```

`apps/web` holds `GATEWAY_URL` and no other backend address.

## 4. The adjudication pipeline

> **Amended 2026-08-22 — stage 1 is struck. See
> [ADR-0018](../decisions/0018-no-normalize-stage.md).** A prior-authorization request arrives
> with its procedure and diagnosis codes already assigned by the submitter's billing system;
> there is no free-text-to-code step in this domain. Deriving the code with a model would also
> breach invariant 2 at the one point nothing downstream re-checks, and the deterministic half
> of the stage ("validate codes: table lookup") has no licensable implementation for CPT
> (ADR-0004). What free text is genuinely needed for is *retrieval* — a cross-encoder cannot
> rank a bare-code query, measured — and that is `cases.request_text`. The stage is left in
> the diagram below, struck, rather than deleted: the spec was approved, and this should read
> as a decision rather than as an omission.

```
POST /cases  →  202 + case_id                        (enqueued on Redis Streams)
      │
      ▼  worker
 1. normalize        STRUCK — ADR-0018. A case arrives carrying requested_code and icd10;
                     request_text carries the narrative the policy search ranks on.
 2. eligibility      member svc, SQL         → inactive?  ESCALATE not_eligible
 3. governing policy policy svc, effective-dated → none?  ESCALATE no_governing_policy
 4. decompose        LLM, citation-constrained → [C1..Cn], each citing its source chunk
 5. verify each Ci   IN PARALLEL
       type=deterministic  → member svc SQL
       type=judgment       → hybrid retrieval + LLM over clinical notes
       each returns: met | not_met | insufficient_evidence, confidence, evidence spans
 6. aggregate        ASYMMETRIC GATE
       all met AND all confidences ≥ threshold  → APPROVE
       any not_met                              → ESCALATE
       any insufficient_evidence                → ESCALATE
       (no branch produces a denial)
 7. persist          determination + criterion results + append-only events
      │
      ▼
 escalated → clinician work queue, evidence packet pre-assembled
      │
      ▼
 clinician decides → reviews.agreed_with_system → flywheel into evals
```

**Step 5 is the agentic core.** N criteria means N targeted verifications, each choosing its
own tool and its own query against a different part of the record, based on what step 4
produced. The model was never shown this policy before and still emits correct, checkable
tool calls.

**Refusal is explainable per criterion.** Not `low_retrieval_score` but:

> Escalated — criterion 2 (documented moderate or severe OSA) has insufficient evidence in
> the record. Criteria 1, 3, 4 met.

### Criterion types

| type | verified by | example |
| --- | --- | --- |
| `threshold` | SQL comparison | apnea events ≥ 30 |
| `enum` | SQL membership | sleep test type ∈ {I, II, III, IV+airflow} |
| `temporal` | SQL date math | coverage active on DOS; no duplicate within N months |
| `judgment` | retrieval + LLM over notes | documented moderate/severe OSA; adherence and benefit |

The model classifies each extracted criterion into one of these and emits the parameters.
It never performs the comparison itself.

## 5. Data model

```
policy          policies(id, ncd_id, version, title, effective_from, effective_to, source_url)
                chunks(id, policy_id, heading_path, text, embedding)

member          members(id, plan, coverage_start, coverage_end)
                sleep_studies(id, member_id, test_type, apnea_events, ahi, channels, date)
                procedures(id, member_id, code, date)
                encounters(id, member_id, date)
                notes(id, encounter_id, text)

adjudication    cases(id, member_id, requested_code, icd10, date_of_service, status, kind)
                criteria(id, case_id, ordinal, text, type, params, source_chunk_id)
                criterion_results(id, criterion_id, verdict, confidence, tool, evidence)
                determinations(id, case_id, outcome, blocking_criteria, thresholds)
                reviews(id, case_id, clinician_id, outcome, rationale, agreed_with_system)
                case_events(id, case_id, seq, type, payload, created_at)   -- append-only

evals           golden_cases(id, fixture, expected_outcome, expected_criteria, author)
                eval_runs(id, model, prompt_version, thresholds, git_sha, started_at)
                eval_results(id, run_id, golden_case_id, outcome, criterion_scores)

auth            users(id, email, password_hash, role)      -- clinician|reviewer|operator|admin
                sessions(id, user_id, expires_at)
```

`case_events` is append-only and is the audit trail. `reviews.agreed_with_system` is the
flywheel column: one boolean that converts clinical work into eval data.

## 6. Events and Redis

Each pipeline stage appends a `case_events` row (truth) and publishes to Redis (delivery):

- **Redis Streams** — work: `case.adjudicate`, `policy.ingest`, `eval.run`. Consumer groups,
  retry, survives restart.
- **Redis Pub/Sub** — live fan-out of step events to SSE subscribers on the console.
- **Redis** — session cache and rate-limit buckets.

The event log is event-driven because regulation demands a reproducible record of how a
decision was reached, not because event-driven is fashionable. State is *not* derived by
replaying events; Postgres rows remain authoritative. See ADR-0005.

### SSE step audit

```
event: step       {"step":"eligibility","status":"ok","source":"member/sql"}
event: step       {"step":"policy","status":"ok","ncd":"240.4","effective":"2026-01-01"}
event: step       {"step":"criteria","status":"ok","count":4}
event: criterion  {"id":"C1","verdict":"met","tool":"sql","evidence":[...]}
event: criterion  {"id":"C2","verdict":"insufficient","tool":"retrieval+llm","evidence":[...]}
event: decision   {"outcome":"escalate","blocking":["C2"]}
```

The reviewer watches the machine show its work rather than receiving a verdict. This is the
audit surface Texas requires, rendered live, and it is how clinical software earns trust.

## 7. Evaluation

Two levels, where the predecessor had one.

**Criterion level** — criteria extraction precision/recall against a human-authored criteria
list per policy; verdict accuracy per criterion; whether the cited span is correct.

**Case level** — the business number:

| metric | unit |
| --- | --- |
| auto-approval rate | % |
| wrongly auto-approved | × average claim amount = currency |
| wrongly escalated | × review minutes × loaded clinician rate = currency |

The threshold sweep carries over from Deflect, but the y-axis becomes **money**: plot total
cost against the confidence threshold and choose the minimum. The operating point is then an
argument rather than a preference.

**Signature ablation.** Run the full pipeline with the LLM performing the date math and
threshold comparisons instead of SQL, and publish the error rate. This proves invariant 2
empirically rather than asserting it. It is the counterintuitive measured finding that gives
the project its spine, and it concerns a decision that matters.

**Golden set.** Human-authored labels only. Synthetic members are generated; expected
determinations are written by a person reading NCD 240.4. Target for v1: 60 cases, of which
at least 20 must escalate and at least 8 are *near-miss* — in-domain, partially satisfied,
where the policy nearly but does not quite support approval. Deflect's refusal set was
entirely out-of-domain, which made refusal look easier than it is; that mistake is not
repeated. See ADR-0009.

## 8. Data sources

| source | use | licence |
| --- | --- | --- |
| CMS Medicare Coverage Database (NCD/LCD bulk download, CSV) | policy corpus | Public domain, but the download requires accepting ADA/AMA/NUBC terms — **CPT descriptions are AMA-copyrighted and are never committed**. Codes as identifiers only. |
| Synthea | synthetic members: conditions, encounters, procedures | Apache 2.0, no PHI |
| Layered narrative notes | clinical text for judgment criteria, generated *seeded from* the Synthea record so both stay consistent | generated |

No real patient data at any point.

## 9. Stack

Python 3.12, FastAPI, SQLAlchemy async, Alembic, uv, Postgres + pgvector, Redis, Next.js.
Local models via Ollama — Qwen2.5-14B-Instruct for structured output, with the existing
bge-small embedder and ms-marco-MiniLM reranker retained. The provider abstraction stays, so
a hosted model can be swapped in for headline eval runs with a config change.

Services refuse to start if the configured model cannot produce schema-constrained output —
misconfiguration fails at boot, never on the first request.

## 10. Testing

Critical paths require tests: the asymmetric gate, criteria extraction, each criterion-type
verifier, the deterministic tools, and the append-only guarantee on `case_events`. CRUD and
wiring do not. A critical-path change without a test is not complete.

The gate deserves a test asserting that **no input produces a denial** — the invariant should
fail loudly if someone ever adds the branch.

## 11. Deliberately not in v1

No Kubernetes, no service mesh, no distributed tracing backend, no hosting deployment. No
appeals workflow. No provider portal. Deployment is deferred, not forgotten.
