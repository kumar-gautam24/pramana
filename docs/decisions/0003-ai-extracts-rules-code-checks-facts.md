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
