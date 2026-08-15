# ADR-0002 — The system has no deny path

**Status:** accepted, 2026-08-15

## Context

Prior authorization has three outcomes: approve, deny, pend to a human. California SB 1120
("Physicians Make Decisions Act") requires that any denial, delay or modification on
medical-necessity grounds be decided by a licensed physician. The Medicare Advantage rule
requires a health professional to review; Illinois permits only a clinical peer to issue an
adverse determination.

## Decision

The system emits exactly two outcomes: `APPROVE` and `ESCALATE`. No code path produces a
denial — not behind a feature flag, not in test helpers. Adverse determinations are made by
a human holding the `clinician` role.

## Consequences

Failure modes are bounded. A wrong approval costs money; a wrong escalation costs clinician
time; neither withholds care from a member. That bound is precisely what makes automating
the approvals defensible, and it is the central argument of the project.

The gate becomes asymmetric rather than a symmetric threshold, which is a more interesting
and more honest design than the predecessor's single scalar cutoff.

A test asserts that no input produces a denial, so the invariant fails loudly if anyone adds
the branch later.

Cost: the system cannot realise the savings of automated denial. That is intentional, and in
several jurisdictions it is also the law.
