# ADR-0016 — A fact declares the values the record can hold

**Status:** accepted, 2026-08-19

## Context

Extraction produces criteria whose `params` name a **fact** — `ahi`, `test_type`,
`study_date`, `coverage_active` — and a deterministic verifier fetches that fact from `member`
and compares it. The fact names are a closed vocabulary drawn from `MemberClient`'s own
surface, which is a wire contract between two services and not a per-policy vocabulary
(invariant 3 stands: nothing here branches on a particular NCD).

Asked to decompose the real NCD 240.4 from the real corpus, a real model produced a correct
`test_type` criterion whose `allowed` list was written in the **policy's** words:

```
["attended PSG", "Type II HST", "Type III HST", "Type IV HST (>=3 channels)"]
```

`member` stores `attended_psg`, `home_type_ii`, `home_type_iii`, `home_type_iv`. Zero overlap.

The enum verifier compared the member's value against that list and returned NOT_MET, so every
case failed its test-type criterion and **every case escalated** — including the one that
should have approved. Both sides were individually right. The policy really does say "Type II
HST"; the database really does say `home_type_ii`; nothing in between had ever been asked to
reconcile them.

No test saw it, and could not have. The hand-authored extraction fixture used values that
happened to match the verifier, so it agreed with itself. This is the same shape as every other
serious defect on this branch: a seam between two correct components, invisible to unit tests,
visible only when a real model meets the real database.

The same argument applies one level up. A flat set of fact names let nine nonsense combinations
validate — a `threshold` criterion on `test_type` has no numeric value to compare, and a
`threshold` on `condition_codes` type-checks but cannot be fetched, because nothing supplies
which codes to count.

## Decision

A fact is declared by a `FactSpec`, not by membership in a set. Each one carries:

- **`datatype`** — number, string or date;
- **`permitted_types`** — which criterion types may name this fact at all;
- **`fetch_args`** — extra arguments `member` needs before the fact can be fetched
  (`adherence_fraction` cannot be answered without `min_hours` and `window_days`);
- **`permitted_values`** — for facts whose vocabulary is closed, the exact strings the member
  record can hold. `None` means genuinely open.

The vocabulary is shown to the model in the prompt **and** enforced after the answer comes
back: `validate_params` rejects an `allowed` list containing anything outside it. A model that
writes policy prose therefore produces a loud `ExtractionInvalid` — and an escalation that says
the system could not read the policy — instead of a silent, confident "not met" about a study
that was in fact valid. Same discipline as `source_chunk_id`'s closed enum in extraction:
constrain the answer, then check it anyway.

Two boundaries are drawn deliberately:

**`condition_codes` stays open.** SNOMED is unbounded, and enumerating the codes that happen to
be in the seeded corpus would reject a correct criterion about any condition no test member
has — turning a coverage question into an artefact of the fixture data. The `allowed` list
there is a query, not a claim about what exists.

**A closed vocabulary describes what the record can hold, never what a policy should accept.**
`test_type` includes `actigraphy`, which no coverage determination would ever accept as a
diagnostic study, because `member` can emit it. Pruning it to the four "acceptable" types would
be a policy judgment smuggled into a fact declaration. Which study types qualify is the
extracted criteria's business.

## Consequences

The failure mode this closes is the worst kind this system has: a wrong verdict delivered with
full confidence and a plausible audit trail. It now surfaces as a refusal to decide.

The vocabulary is a wire contract between `adjudication` and `member`, so it can drift. It is
sourced from `member`'s **generator**, not from the rows that happen to be seeded, and a test
asserts the two agree. That distinction was learned the hard way: the first attempt at this
vocabulary was read off the seeded rows, missed `attended_psg` and `actigraphy`, and was caught
only by the suite.

Nine unfetchable or nonsensical fact/type combinations now fail validation in one place instead
of being rejected ad hoc by whichever validator happened to notice.

The cost is that adding a fact is no longer a one-line addition to a set. That is the intent: a
fact with no declared datatype, no permitted types and no stated vocabulary is a fact nobody
has checked can actually be answered.

Related: [ADR-0003](0003-ai-extracts-rules-code-checks-facts.md),
[ADR-0011](0011-alternative-criteria-sets.md),
[ADR-0014](0014-one-schema-three-providers.md)
