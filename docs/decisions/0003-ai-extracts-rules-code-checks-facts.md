# ADR-0003 — AI decides what the rules are; deterministic code checks the facts

**Status:** accepted, 2026-08-15

## Context

Hand-coding logic for thousands of payer policies is what makes legacy utilization-management
systems slow and expensive. The system must read a coverage determination it has never seen.
That requires a model.

But some criteria reduce to exact questions: was there a prior study within twelve months, is
coverage active on the date of service, were there at least thirty apnea events. A language
model answering those is slower, costlier, non-reproducible, and wrong in a way nobody can
see.

## Decision

The model extracts criteria from policy prose, classifies each into a type
(`threshold`, `enum`, `temporal`, `judgment`) and emits its parameters. Comparisons of the
first three types are performed in SQL. Only `judgment` criteria — those requiring
interpretation of clinical narrative — are decided by the model.

## Consequences

Nothing is hardcoded per policy: the model chooses which verified tool to call and with what
arguments, so an unseen determination is still handled. Generalisation and auditability are
both preserved rather than traded off.

A model that is confidently wrong about a date is exactly the failure this project exists to
prevent; using one where determinism was available would contradict the project's own thesis.

This claim is tested empirically rather than asserted: an ablation runs the pipeline with the
model performing date and threshold comparisons, and publishes the error rate.

### The ablation has been run once, and produced no error rate — 2026-08-22

Both arms, same commit (`f82fb4a`), same model (`openai/gpt-oss-120b`), the same five golden
cases, differing only in `run_mode`. Sample size five, against ADR-0009's target of sixty.

The case-level delta came out **exactly zero on every metric**, with no disagreements, over a
five-case intersection the comparison endpoint certified `comparable`. **That zero is an
artifact and is recorded here as one, not as a result.** The ablated arm reached the gate on
**none** of its five cases: substituting a model call for each deterministic comparison
multiplies the calls per case, and against the provider's ceiling of 8,000 tokens per minute
every case exhausted its retry ladder and short-circuited `upstream_unavailable`. The baseline
reached the gate on four of five. An arm that adjudicated nothing scored identically to one
that adjudicated most of the set.

Three things this bought, none of them the number this ADR is owed:

- **The harness could price an outage as a determination.** `evals.runner` read the `outcome`
  of an `upstream_unavailable` short-circuit like any other, so a rate limit was scored as a
  clinical escalation. Fixed the same day; it contradicted that module's own docstring. The one
  cost figure this project had ever published was partly built on it.
- **`comparable: true` does not mean the comparison means anything.** The endpoint checks that
  two runs share a commit, model and prompt version and differ only in their ablation. These
  did. Nothing checked that either arm produced an adjudication, so the guard designed to stop
  a misleading delta certified this one.
- **The ablation is not runnable on a rate-limited key**, which is a property of the experiment
  rather than of the thesis. Whatever finally measures this needs a token budget that lets the
  expensive arm finish, and the retry budget and the harness's case timeout have to move
  together with it.

No error rate appears in this ADR or in the README until two arms both reach the gate.
