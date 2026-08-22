# ADR-0018 — The `normalize` stage is struck from the design

**Status:** accepted, 2026-08-22

## Context

The approved design numbers the adjudication pipeline from a first stage:

```
 1. normalize        LLM, structured output  → CPT + ICD-10
    validate codes   deterministic table lookup
```

It was never built. `cases.requested_code` and `cases.icd10` have been `NOT NULL` since
migration 0001, so a case arrives carrying its codes and there is no free text for a
`normalize` stage to act on. Plan 04 task 7 deferred the question rather than deciding it,
with an owner named: whoever builds intake either adds the stage or strikes it from the spec.
Plan 07 built the console but not intake, and passed the same instruction on. This session
built intake, so the question is now due.

Three things are known now that were not when the stage was written down.

**The submitter already holds the codes.** A real prior-authorization request arrives as an
X12 278 services-review transaction or through a payer portal form. Both carry the procedure
code and the diagnosis code as structured fields, because they come from the provider's
billing system — which assigned those codes already, since it will bill on them. There is no
free-text-to-code step at intake in prior authorization. The stage models a workflow the
domain does not have.

**Deriving a code with a model would breach invariant 2.** The invariant splits the work: the
model decides what the rules are, deterministic code checks the facts they point at. A model
turning narrative into a billing code is neither. It produces a *fact* — the identity of what
was requested — and nothing downstream re-checks it. Every subsequent stage is correct with
respect to that code: the policy search finds the determination governing it, the criteria are
extracted from that determination, the verifiers compare the member's record against those
criteria. A wrong code does not produce a wrong-looking answer. It produces a confident,
fully-evidenced, correct-looking answer to the wrong question, and the audit trail records the
whole thing as sound. That is precisely the failure mode invariant 2 exists to prevent, and it
would sit at the one place in the pipeline where nothing can catch it.

**The deterministic half cannot be built as specified.** "validate codes: deterministic table
lookup" needs a code table. For CPT that table cannot be committed — the CMS download carries
an AMA licence, and invariant 8 / [ADR-0004](0004-cms-corpus-and-cpt-licensing.md) forbid it.
(The design's own worked example, `E0601`, is HCPCS Level II rather than CPT, which is a
further sign the stage was specified loosely.) So the guard that was supposed to catch the
model's mistake is the half that has no licensable implementation.

**What free text is actually for was measured, and it is not this.** Retrieval against the
real corpus, dated 2026-01-15, limit 8:

| query | criteria-bearing chunks retrieved (of 57, 58, 59, 69, 70) |
| --- | --- |
| `E0601 G47.33` — the codes alone | 1 |
| "continuous positive airway pressure coverage criteria for obstructive sleep apnea" | 4 |
| "apnea hypopnea index threshold for CPAP coverage" | 3 |

A cross-encoder cannot rank a bare-code query: codes are out of distribution for a model
trained on question/passage pairs, and the codes-only scores sit in a flat band around −11,
which is the reranker saying *nothing here matches*. The narrative is load-bearing — but as
**retrieval input**, not as a source for the codes. That is the `request_text` column
(migration 0002), which already exists and is already used.

The design conflated two claims: "intake needs free text" (true, and shipped) and "the codes
must be derived from that free text" (not true, and not safe).

## Decision

**The `normalize` stage is struck from the design.** A case is submitted with its
`requested_code` and `icd10` as structured fields, and with an optional clinical narrative in
`request_text` whose only job is to give the policy search something a cross-encoder can rank.
No model is asked what was requested.

`docs/specs/2026-08-15-pramana-design.md` carries an amendment note at the pipeline diagram
and at the SSE example pointing here, rather than being silently edited: the spec was
approved, and a struck stage should read as a decision, not as an omission.

The pipeline is six stages: `started → eligibility → policy → criteria → criterion → decision`.

## Consequences

The console's intake form asks for the codes and says, at the narrative field, what the
narrative is for and what was measured without it. A submitter who leaves it blank still gets
a decision — the pipeline falls back to the codes-only query — but a worse one, and the form
says so rather than letting a blank field look free.

If a future submission channel genuinely arrives without codes (a fax queue, a patient-facing
form), this decision is the thing to reopen. The reopening condition is specific: such a stage
needs a licensable code table for the deterministic check, and it needs the derived code to be
visible to the reviewer as a model-produced field rather than as part of the request. Neither
exists today.

The design's step count changes, so `CLAUDE.md`, `README.md` and the SSE example in the spec
all now describe six stages. The `case_events.type` vocabulary is unchanged: `normalize` was
never one of its values.

Related: [ADR-0003](0003-ai-extracts-rules-code-checks-facts.md),
[ADR-0004](0004-cms-corpus-and-cpt-licensing.md),
[ADR-0007](0007-reranker-produces-the-score.md)
