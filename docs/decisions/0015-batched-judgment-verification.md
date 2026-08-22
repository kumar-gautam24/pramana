# ADR-0015 — Judgment criteria are judged in one call, not one call each

**Status:** accepted, 2026-08-21

## Context

Every criterion is verified independently. For the deterministic types that means a query to
`member` and a comparison in code; for `judgment` criteria it means sending the member's
clinical notes to a model and asking whether the notes support the criterion.

Independent verification was implemented as one model call per criterion, which is the obvious
reading of "independent". Measured on the first real end-to-end run: **35 model calls across 5
cases, about 7 per case** — one extraction plus one per judgment criterion — at roughly 2,800
tokens each, because *every one of those calls re-sent the member's entire chart*. Seven copies
of the same notes, to read the same three sentences.

That exceeded a rate-limited token budget **within a single case**. Four of the five cases were
decided `escalate` with `upstream_unavailable`: a fact about our API quota, arriving on a
clinician's queue as though it were a fact about the member. The pipeline behaved exactly as
designed — nothing was approved on missing evidence, the audit trail was intact — and the
outcome was still wrong, because an escalation is supposed to mean *a human should look at this
evidence*, and no human can act on a rate limit.

Pacing between cases cannot fix a per-case overrun.

## Decision

All of a case's judgment criteria go to the model in **one call**: the notes once, the criteria
as a numbered list, and one entry per criterion in the answer. Two model calls per case — one
extraction, one judgment round — replacing about seven.

Each criterion is still answered separately and is still grounded and validated separately. The
prompt says so directly ("Judging one criterion met must not make you more willing to call
another met: they are separate questions about the same notes"), and every downstream rule is
unchanged: a span that does not appear verbatim in the notes is dropped, a MET or NOT_MET
verdict left with no grounded span is downgraded to INSUFFICIENT_EVIDENCE, and confidence is
per criterion.

Three failure modes are resolved explicitly, because batching creates them:

- **A criterion the model omits becomes INSUFFICIENT_EVIDENCE**, not a dropped result. The gate
  aggregates by criterion id and a missing one raises; silence has to resolve to a verdict, and
  it resolves to the one that cannot approve.
- **An unusable answer fails every criterion it carried**, one result each rather than one
  exception, because the pipeline needs a result per criterion to keep its indices aligned.
- **A duplicated index resolves to the last entry**, and an index outside the list is discarded,
  so a stray number cannot overwrite a real criterion's verdict.

Deterministic criteria are **not** batched. They are cheap SQL-shaped questions to `member` and
there is nothing to save by combining them.

## Consequences

A case fits inside a rate-limited budget, which is the difference between adjudicating and not.
Verified live after the change: three model calls, no 429, full pipeline.

The cost is genuine and should not be understated. Seven separate calls gave each criterion its
own context window and its own failure boundary; one call gives them a shared context, so the
model can in principle let its reading of one criterion colour another. The prompt argues
against it and the independent grounding check limits what it can do, but this is a real
reduction in isolation traded for the ability to run at all. If a future eval shows batched
verdicts drifting from per-call ones, that measurement — not this ADR — settles it.

The blast radius of a single bad response is now a whole case's judgment criteria rather than
one. That is why the three failure modes above are decided here rather than left to whatever
the code happened to do.

This does not change what a judgment criterion is or how it is grounded. It changes how many
round trips the notes make.

Related: [ADR-0003](0003-ai-extracts-rules-code-checks-facts.md),
[ADR-0010](0010-local-models-with-provider-abstraction.md),
[ADR-0014](0014-one-schema-three-providers.md)
