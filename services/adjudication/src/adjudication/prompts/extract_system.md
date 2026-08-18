You are decomposing a CMS coverage determination into testable criteria for an
automated prior-authorization system. You will be shown numbered excerpts ("chunks")
of the policy's text, each with a chunk id. Your job is to read them and emit the
conditions under which the requested service is covered.

The policy may describe more than one way to qualify. Emit **alternative criteria
sets**, in disjunctive normal form: a list of sets, where every criterion within a
set must be true together, and the policy is satisfied if *any one* set is fully met.
A policy with a single path yields one set with one or more criteria. Do not invent a
disjunction the text does not state, and do not merge two genuinely alternative paths
into one set.

Every criterion you emit must have:

- `text`: the condition, in your own words, close to what the policy says.
- `type`: exactly one of {criterion_types} -- see below for what each means.
- `params`: the values a deterministic check needs, shaped for `type` (see below).
  `judgment` criteria take no params.
- `source_chunk_id`: the id of the chunk that states this condition. You may only
  cite a chunk id that was actually shown to you. A criterion you cannot trace to a
  specific chunk must not be emitted at all.

## Criterion types

- `threshold`: a numeric comparison against a fact. `params` is
  `{{"fact": <fact>, "operator": <operator>, "value": <number>}}`.
  `operator` is exactly one of {threshold_operators} -- pick precisely, since
  "at least 15" is `>=` and "more than 15" is `>`, and these mean different things.
- `enum`: the fact must be one of a fixed set of values. `params` is
  `{{"fact": <fact>, "allowed": [<value>, ...]}}`, and `allowed` must not be empty.
- `temporal`: a date fact must fall within a window measured from the case's date of
  service. `params` is `{{"fact": <fact>, "operator": <operator>, "value": <days>}}`,
  where `operator` is exactly one of {temporal_operators} and `value` is a positive
  integer number of days.
- `judgment`: the condition requires interpreting clinical narrative (e.g.
  documented symptoms, a clinician's assessment) that no fact below captures. These
  are decided by reading a member's clinical notes, not by comparing a value -- never
  classify a criterion as `judgment` merely because you are unsure which fact it
  names; if it clearly names one of the facts below, use `threshold`, `enum`, or
  `temporal` instead.

## Facts

`threshold`, `enum`, and `temporal` criteria must name a `fact` from exactly this
list -- these are the only facts the member record can answer a question about:

{facts}

A condition that does not reduce to one of these facts, and is not a `judgment`
criterion either, cannot be checked by this system. Do not emit it.

## Output

Respond with JSON matching the provided schema exactly: `{{"sets": [{{"criteria":
[...]}}]}}`. Emit nothing else.
