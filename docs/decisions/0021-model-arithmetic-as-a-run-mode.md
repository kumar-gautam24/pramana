# ADR-0021 — The `model_arithmetic` ablation is a run mode on the case

**Status:** accepted, 2026-08-22

## Context

[ADR-0003](0003-ai-extracts-rules-code-checks-facts.md) is this project's central claim: the
model decides what the rules are, and deterministic code checks the facts they point at,
because a model doing arithmetic "fails silently and confidently, which is the exact failure
this project exists to prevent". Its consequences section does not leave that as an assertion.
It promises a measurement: *an ablation runs the pipeline with the model performing date and
threshold comparisons, and publishes the error rate.*

`evals` was built with the column for it — `eval_runs.ablation` has carried
`CHECK (ablation IN ('none', 'model_arithmetic'))` since its first migration, with a comment
saying a run and its ablated twin "differ in this column and nothing else, which is what makes
the comparison an argument rather than an anecdote". But `adjudication` had no such mode, so
`POST /eval-runs` answered **501** rather than publish a figure labelled "model arithmetic"
that SQL had produced. Refusing was right; leaving it refused indefinitely is not, because
the unmeasured claim is the one the whole design rests on.

The question this ADR settles is not *whether* to build it but *where* the difference between
the two arms is allowed to live. A second pipeline, or a second verifier module, would drift:
the two arms would end up differing in their fetches, their missing-evidence rules, or their
evidence shapes, and any number the comparison produced would be about that drift as much as
about the arithmetic.

## Decision

**The ablation is a run mode on the case, and the difference lives in exactly one module.**

`cases.run_mode` is `deterministic` or `model_arithmetic`, `NOT NULL DEFAULT 'deterministic'`
with a CHECK. On the case rather than on the request because the worker receives an id off a
Redis stream and nothing else — a mode travelling beside the case could disagree with what the
audit trail says was run. Not nullable, because "decided by deterministic code" and "nobody
recorded how this was decided" must not be the same value on this column of all columns.

`services/verify/arithmetic.py` holds an `Arithmetic` protocol with three methods —
a numeric comparison, a membership test, and a date-window test — and two implementations.
`PythonArithmetic` is the code that was already in `deterministic.py`, moved rather than
rewritten. `ModelArithmetic` asks the model. `services/verify/__init__.py::_arithmetic` is the
only place `run_mode` is read; nothing in the pipeline branches on it.

`deterministic.py` keeps everything else and is shared by both arms: the fetches, the
missing-versus-contradicted rules, the "any study before the date of service satisfies it"
semantics, the evidence dictionaries, and confidence `1.0`.

Four things were deliberately held constant that a looser ablation would have varied.

**Confidence stays 1.0 on both arms.** A model self-report would be a second difference, and
it would move the ablated run along the threshold sweep for a reason unrelated to whether its
arithmetic was right. The model's answer is kept in the evidence, where it informs a reader
without entering the gate.

**The adherence window is computed in Python on both arms.** It is an argument to the *fetch*,
not the comparison that produces the verdict. Ablating it would change what evidence the two
arms saw, which is the one difference that would make the comparison meaningless.

**One model call per comparison, not batched.** Batching judgment criteria was a clear win
(ADR-0015) because each was a separate question about one shared document. These are separate
questions about *different numbers*, and one context would let one answer condition the next,
changing what is measured. `ModelArithmetic` does serialise its calls behind a lock, because
`verify_all` gathers a case's criteria concurrently and a thundering herd of model calls would
have the ablation measuring a rate limit; that changes latency, never an answer.

**The prompts ask the bare question.** No worked example, no step-by-step instruction, no
policy framing, temperature 0, a two-key schema. That is the framing most favourable to the
model, which makes any error rate measured here a *lower bound* on what a system built this
way would produce.

**Two things the ablation cannot cover, reported rather than hidden.**

`condition_codes` criteria have no comparison step. `MemberClient.conditions` takes the codes
as a query parameter and `member` filters in SQL, so the fetch *is* the membership test, and
there is no way to ask that endpoint for every condition a member has. Those criteria fall
back to the ordinary path and keep their unprefixed `tool`. Every report carries
`ablation_coverage`: how many comparison-bearing criteria the run had, and how many the model
actually decided. A partial ablation reported as a whole one would overstate the experiment.

A model that answers a comparison with something unreadable raises `ArithmeticUnusable` and
becomes `INSUFFICIENT_EVIDENCE`, not `NOT_MET`. A wrong answer belongs in the error rate; a
missing answer is a different finding, and folding it into "not met" would make the ablated
arm look merely strict rather than broken.

## Consequences

`POST /eval-runs` no longer answers 501 for `model_arithmetic`. `runner` submits every case
with the `run_mode` its run's `ablation` maps to, through a one-entry-per-member table, so an
`Ablation` value added without a run mode is a `KeyError` when the run starts rather than a
run that adjudicates every case the ordinary way while its column claims otherwise.

**An ablated determination is labelled at every layer it touches**: the `cases.run_mode`
column, the `started` event's payload, a `model_arithmetic:` prefix on each ablated
criterion's `tool`, and a red banner at the top of the case screen. It is also role-gated —
`POST /cases` refuses the mode unless the caller's gateway-established role satisfies
`operator`, which is why `evals` states that role explicitly when it calls adjudication
directly. The gating is defence in depth; the labelling is the thing that keeps an
experimental result from being read as an adjudication.

A golden case's fixture may not carry `run_mode` (it belongs to the run) or
`idempotency_key`. The second is the subtler of the two: `POST /cases` is idempotent on that
key, so a run and its ablated twin submitting the same fixture would be handed *the same
adjudication case*, and the twin would score the first run's determination — reading as
perfect agreement between the two arms. Both are rejected at authoring time.

An ablated case is slower and costs more: one model call per comparison, serialised, on top of
extraction and the judgment round. That cost is the finding rather than an overhead to
optimise away, and the worker's retry ladder (ADR-0020) is what keeps a rate limit from
turning it into an unmeasured case.

**The comparison is an endpoint, not an exercise for the reader.**
`GET /eval-runs/{id}/comparison?against={other}` puts two runs side by side and produces the
signed delta — but only when they differ in their ablation and in nothing else. When they
differ in more, both runs' own figures are still returned and the **delta is withheld rather
than zeroed**, with the offending fields named: a delta is a claim about causation, and there
is none to make across several simultaneous changes. This is the same discipline as the run
mode itself, one level up, and it is where the discipline can actually be broken by accident
— nothing else stops an operator reading the difference between a run on one model and a run
on another as the cost of model arithmetic.

Two smaller choices in that endpoint follow from the same argument. Every figure is computed
over the **intersection** of the golden cases both runs decided, because a run that timed out
on its two hardest cases would otherwise look cheaper than the run that finished them; the
cases only one arm reached are listed rather than dropped. And orientation is read off each
run's `ablation` rather than off which id is in the path, so the sign of the delta cannot
depend on which way round a caller named the pair.

The number this produces is not yet in the README, and must not be written there until a run
has actually been executed. What exists today is the apparatus.

Related: [ADR-0003](0003-ai-extracts-rules-code-checks-facts.md),
[ADR-0015](0015-batched-judgment-verification.md),
[ADR-0020](0020-retry-transient-upstream-failures-in-the-worker.md)
