# ADR-0011 — A policy decomposes into alternative criteria sets

**Status:** accepted, 2026-08-16

## Context

The gate merged in plan 01 requires **every** criterion to be met before it approves. Reading
the real text of NCD 240.4, fetched from the Coverage API on 2026-08-16, showed that this
cannot express what a coverage determination actually says:

> An initial 12-week period of CPAP is covered in adult patients with OSA if **either** of the
> following criterion using the AHI or RDI are met:
> - AHI or RDI greater than or equal to 15 events per hour, **or**
> - AHI or RDI greater than or equal to 5 events and less than or equal to 14 events per hour
>   **with** documented symptoms of excessive daytime sleepiness, impaired cognition, mood
>   disorders or insomnia, **or** documented hypertension, ischemic heart disease, or history
>   of stroke.

Diagnosis is disjunctive too: a clinical evaluation **and** a positive result from *one of*
four sleep-test types.

A policy is a boolean expression, not a flat list. A conjunction-only gate would refuse cases
the policy plainly covers — which is the expensive failure here, because every wrongly
escalated case costs clinician time.

## Decision

Criteria extraction emits **one or more criteria sets**, in disjunctive normal form. Every
criterion within a set must be met; the policy is satisfied if **any one set** is fully met.

For NCD 240.4's initial authorisation, that is three sets:

```
set 1: [valid sleep test, coverage active, AHI >= 15]
set 2: [valid sleep test, coverage active, AHI 5-14, documented symptoms]
set 3: [valid sleep test, coverage active, AHI 5-14, documented comorbidity]
```

`evaluate_gate` is unchanged and runs once per set. A thin layer above it approves if any set
approves, and escalates otherwise.

**On escalation, report the blocking criteria of the *closest* set** — the one with the fewest
unmet criteria. A reviewer needs to know which single document would have settled the case,
not the failures of the path the member was never on.

Extraction is capped at a fixed number of sets. A policy that expands beyond it escalates
rather than being partially evaluated: a determination the system could not fully represent is
exactly the kind it must not decide.

## Consequences

The plan 01 gate survives intact, along with the exhaustive test proving no input produces a
denial. Disjunction is handled entirely above it, so the invariant that matters most is not
re-opened.

Disjunctive normal form is the reason this stays simple: no tree walker, no operator
precedence, no partially-evaluated boolean state to reason about in an audit. The cost is that
a policy with many independent disjunctions multiplies out, which is what the cap bounds.

The rejected alternative was a criteria tree with `all_of`/`any_of` nodes. It is closer to how
a policy reads, but it changes the gate's contract, requires a recursive evaluator, and makes
"which criterion blocked this?" a tree-shaped answer rather than a list a reviewer can act on.

Related: [ADR-0002](0002-no-deny-path.md), [ADR-0003](0003-ai-extracts-rules-code-checks-facts.md)
