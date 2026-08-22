# ADR-0019 — What a clinician may record: approve, deny, pend

**Status:** accepted, 2026-08-22

## Context

`determinations.outcome` is `CHECK (outcome IN ('approve', 'escalate'))`. That constraint is
[ADR-0002](0002-no-deny-path.md) made structural: the database is incapable of holding a
machine-issued denial, so no future code path can produce one by accident.

`reviews.outcome` is the other side of that rule. A licensed clinician *may* issue an adverse
determination — that is the entire reason `reviews` is a separate table — so it cannot borrow
the two-value CHECK. Plan 04 left it unconstrained deliberately and named plan 07 as the
owner, with an explicit "must not ship open". Plan 07 recorded it as still open: the console
proposes `approve` / `deny` / `more_information` at the one place a review is authored, which
is a constraint in one client, not in the schema, and a second client would not inherit it.

"Any string at all" is genuinely wrong for this column. It is the record of who issued an
adverse determination and what they decided — the fact Illinois law is specifically about —
and it is the join key the eval harness reads through `agreed_with_system`. A column that can
hold `Deny`, `denied`, `DENY` and `deny` cannot answer "how many adverse determinations were
issued last quarter" without a human reading every row.

But the candidate set is not obvious, and plan 07's own instruction was **do not close this by
guessing** — putting values in the schema that no regulator recognises is worse than an open
column, because the open column is honest about not knowing. The candidates in utilization
management are: approve (certify), deny (non-certification / adverse determination), partial
approval (modification), and pend for additional information.

## Decision

`reviews.outcome` is `CHECK (outcome IN ('approve', 'deny', 'pend'))`.

**`approve` and `deny`** need no argument. `deny` is the whole point of the table.

**`pend`** is included. A clinician who cannot decide from the record has to be able to say so.
Without the value, such a clinician either records nothing — and the case leaves the flywheel
with no row, so the one boolean that turns clinical work into eval data is never written — or
records a disposition they did not reach. `pend` is the name the disposition has in
utilization-management practice and in the state prior-authorization statutes that regulate
turnaround times, so it is the name an auditor will already know. It is deliberately not
`more_information`, which describes the follow-up rather than naming the determination.

**Partial approval is excluded, and this is the considered part of the decision.** Two
reasons, and either alone is sufficient.

The first is that a Pramana case cannot express one. A case carries one `requested_code`, one
`date_of_service` and one `kind`. There is no quantity, no duration, no units of service, no
line items. A partial approval is a decision to approve *less than was asked for*, and nothing
in this schema records how much was asked for. The value would be uninterpretable the moment
it was written.

The second is worse. A partial approval is legally an adverse determination as to the portion
refused — that is why Medicare Advantage requires the same notice, appeal rights and clinician
sign-off for a partial as for an outright denial. Recorded as a fourth flat value alongside
`deny`, it would make "was an adverse determination issued on this case" un-answerable by a
query over this column, which is the single question the column most needs to answer.

**The reopening condition is specific**, so this does not have to be re-argued from scratch:
`partial_approval` becomes correct when `cases` carries what was requested in a divisible form
(units, a duration, or line items) **and** the schema can express that a partial is adverse —
either a separate boolean or by splitting the review into per-line dispositions. Adding the
value without both is the thing this ADR refuses.

## Consequences

The constraint is in three places and they cannot drift apart independently:
`migrations/0004_reviews_outcome_vocabulary.sql` holds the CHECK, `ReviewIn.outcome` is a
`Literal` of the same three, and the console's `OUTCOMES` constant carries a comment naming
both. Three copies is one too many, but the alternative — the console importing a Python enum
— is not available across the language boundary, and a generated types step would be a build
dependency for eleven wire shapes (see `apps/web/src/lib/types.ts`).

The migration includes a data migration mapping the console's `more_information` to `pend`,
and **stops with the offending values named** if any row holds something else. It does not
coerce such a row to a default. A row in this table is a licensed clinician's recorded
determination on a real case; a migration that quietly rewrote one would be falsifying the
record the table exists to keep. A failed migration is the correct outcome there.

`reviews.outcome` is now a closed set while `determinations.outcome` is a *different* closed
set, and that asymmetry is the point rather than an inconsistency to tidy up: the machine has
two outcomes and a clinician has three, and the third one is the denial the machine may never
issue.

Related: [ADR-0002](0002-no-deny-path.md), [ADR-0008](0008-human-authored-golden-labels.md),
[ADR-0016](0016-closed-vocabularies-for-facts.md)
