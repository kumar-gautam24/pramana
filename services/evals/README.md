# evals

**The measurement apparatus, and a legal obligation.** Port 8003, database `pramana_evals`.

California requires that utilization-review tools be periodically assessed for accuracy. This
service is that assessment: it runs a set of human-labelled cases through the real adjudication
pipeline and scores what comes back, in dollars and clinician-minutes rather than in accuracy
percentages.

## Data model

| table | holds |
| --- | --- |
| `golden_cases` | a submittable case fixture plus its **human-authored** expected outcome and criteria |
| `eval_runs` | one run: ablation, git sha, model, prompt version, thresholds, status |
| `eval_results` | one row per case per run: outcome, reason, criterion scores, error |

`golden_cases.expected_outcome` is constrained to `approve` or `escalate` — the machine's
vocabulary, because that is what the run is being scored against.

## Labels are human work, by rule

**A model may never author a golden label.** A model grading a model measures agreement, not
correctness, and would void every number this harness produces. The console has an authoring
form; the authoring itself is a person reading the policy.

At least eight cases must be **near-miss** — in-domain and partially satisfied. A refusal set
made only of obviously out-of-scope requests measures the easy half of the problem, which is a
mistake this project's predecessor actually made.

A golden fixture may **not** carry an `idempotency_key` or a `run_mode`. Either one would hand a
run and its ablated twin the same adjudication case, and the twin would score the first run's
determination — reading as perfect agreement between the two arms while measuring nothing.

## How a run works

```
for each golden case:
    submit to adjudication (with this run's run_mode)
    poll until decided or timeout
    read the event trail
    score the outcome against the human label
    score the extracted criteria against the human criteria list
    wait seconds_between_cases
```

**Cases run one at a time, paced.** That is not timidity about concurrency: each case costs
several model calls against a rate-limited provider, and a run that saturated the limit would
measure the token budget rather than the system.

A case that never reaches a determination is recorded with a null outcome and an error, and the
run continues. **Folding an unreachable case into `escalate` would let an outage read as a
correct refusal** — the one way this harness could flatter the system it exists to audit. A case
short-circuited on an unreachable model is therefore counted as unfinished, not as an escalation.

## Two levels of scoring

**Criterion level**, per case: extraction precision, recall and F1 against the human-authored
criteria list, plus the weakest confidence in the case and how much of it was actually ablated.

**Case level**, the numbers that carry units:

```
wrongly auto-approved × average claim amount        (money)
wrongly escalated × review minutes × hourly rate   (time)
```

Both are reported as a count times a named rate, never as a bare total — a figure nobody can
decompose is a number to be believed rather than checked. The rates are configuration, and the
operating point the report recommends is a function of them, so leaving the defaults means
reporting against the defaults' assumptions.

The **threshold sweep** varies the confidence floor and plots total cost, taking the minimum as
the operating point. That makes the chosen threshold an argument rather than a preference. A flat
curve is reported as flat rather than pretending to recommend a threshold.

## The ablation

`POST /eval-runs {"ablation": "model_arithmetic"}` runs every case with the model performing the
threshold, enum and date comparisons instead of Python. This is the experiment behind the
project's central claim, and it exists so the claim can be tested rather than asserted.

`GET /eval-runs/{id}/comparison?against={other}` compares two runs. Three properties of that
endpoint are deliberate:

- **The delta is produced only when the two runs differ in their ablation and in nothing else.**
  Otherwise both runs' own figures are returned and the delta is **withheld with the offending
  fields named** — never zeroed. Two runs of the same arrangement are refused a delta too:
  run-to-run variance is worth looking at and is not an ablation.
- **The intersection is the denominator.** Everything is computed over the cases *both* runs
  decided. Without that, a run that timed out on its two hardest cases looks cheaper than the run
  that finished them. Cases only one arm reached are listed rather than dropped.
- **Orientation comes from the `ablation` field, not from argument order**, so the sign of the
  delta cannot depend on which way round a caller named the pair.

Only cost deltas are coloured in the console. More wrongful approvals and more wrongful
escalations are both worse, in different currencies; colouring them alike would flatten the
asymmetry the project exists to argue about.

## Endpoints

| method | path | purpose |
| --- | --- | --- |
| POST | `/golden-cases` | author a case (label supplied by a human) |
| GET | `/golden-cases` | list the set |
| POST | `/eval-runs` | start a run; returns `202` |
| GET | `/eval-runs` | list runs |
| GET | `/eval-runs/{id}` | the run's full report, including the sweep |
| GET | `/eval-runs/{id}/comparison?against={id}` | compare two runs |
| GET | `/health` · `/ready` | liveness, readiness |

All of these are operator-only at the gateway, which also limits runs to **two an hour**.

## Configuration

| variable | default | purpose |
| --- | --- | --- |
| `DATABASE_URL` | — | storage |
| `ADJUDICATION_URL` | — | probed at startup |
| `GIT_SHA` | — | **stamped onto every run.** Set it deliberately |
| `AVERAGE_CLAIM_AMOUNT` | 1500 | the money axis |
| `REVIEW_MINUTES` | 12 | the time axis |
| `CLINICIAN_HOURLY_RATE` | 180 | loaded rate |
| `SECONDS_BETWEEN_CASES` | 20 | pacing |
| `CASE_TIMEOUT_SECONDS` | 240 | per-case ceiling |

`GIT_SHA` matters more than it looks: two runs with different commits are correctly refused a
delta, so a stale value silently makes a pair incomparable. Start the service with
`GIT_SHA=$(git rev-parse --short HEAD)`.

`CASE_TIMEOUT_SECONDS` and the worker's retry budget are a **pair**. The worker promises that a
retried case still settles inside this window; if either number moves, both must.

The money constants are defaults, not findings. A real assessment needs a citable loaded
clinician rate and a citable average claim amount.

## Running and testing

```bash
cd services/evals
GIT_SHA=$(git rev-parse --short HEAD) uv run uvicorn evals.main:app --port 8003
```

Migrations run automatically at startup for this service.

**This service has no test suite.** That is a known gap, not a claim that it needs none.

## Caveats

- **The comparison endpoint will certify a pair where one arm adjudicated nothing.** It checks
  that two runs share a commit, model and prompt version and differ only in their ablation. It
  does **not** check that either arm produced a determination. Measured: an ablated arm that
  reached the gate on zero of five cases was certified `comparable` against a baseline that
  reached it on four, and reported a delta of exactly zero on every metric. Known and unfixed;
  it is the most important gap in this apparatus.
- **The ablated arm cannot finish on a rate-limited free tier.** It sends a model call per
  deterministic comparison. Against an 8,000 tokens-per-minute ceiling it exhausts its retry
  ladder on every case.
- **Extraction F1 currently returns 0.0 with zero matches on every case.** Whether that is a real
  disagreement about what the policy requires, or a matching rule comparing prose to prose, is
  itself unmeasured — so the metric reports nothing usable rather than reporting that extraction
  failed.
- **The golden set is five cases against a design target of sixty**, at least twenty escalating
  and at least eight near-miss. Every figure this harness can produce is bounded by that.
- **`scripts/migrate.py` cannot be run from this directory** — it imports one of `policy`,
  `member` or `adjudication`. Not currently blocking, because this service migrates itself.
