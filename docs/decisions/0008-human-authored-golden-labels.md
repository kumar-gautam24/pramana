# ADR-0008 — Golden-case labels are human-authored

**Status:** accepted, 2026-08-15

## Context

Synthetic members must be generated — there is no lawful source of real patient records for
this project. It is tempting to generate the expected determinations along with them, since
that would scale the golden set cheaply.

## Decision

Synthetic patients are generated. The expected determination on each golden case is written
by a person reading NCD 240.4 against that patient's record.

## Consequences

If a model generates the cases and a model decides them, the eval measures agreement between
two models rather than correctness — and every number the harness produces becomes
meaningless. The harness is the reason this project is worth building; protecting its
validity outranks the convenience of a larger dataset.

This mirrors the predecessor, whose 80 golden items were hand-written for the same reason.

Cost: the golden set grows slowly. Mitigated by the flywheel — clinician reviews of escalated
cases are human labels produced as a byproduct of work that had to happen anyway, and
disagreements with the system feed straight back into the golden set.
