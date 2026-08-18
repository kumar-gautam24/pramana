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
- `type`: exactly one of {criterion_types} -- see below for what each means and
  which facts it may use. A fact belongs to exactly one of these types; naming it
  under a different one makes the criterion unusable and it will be rejected.
- `params`: the values a deterministic check needs, shaped for `type` (see below).
  `judgment` criteria take no params.
- `source_chunk_id`: the id of the chunk that states this condition, exactly as
  shown in the excerpt below. A criterion you cannot trace to a specific chunk must
  not be emitted at all.

## Criterion types

- `threshold`: a numeric comparison against a fact. `params` is
  `{{"fact": <fact>, "operator": <operator>, "value": <number>}}`, plus `fact_args`
  when the fact requires it (see below). `operator` is exactly one of
  {threshold_operators} -- pick precisely, since "at least 15" is `>=` and "more than
  15" is `>`, and these mean different things. Facts: {threshold_facts}.
- `enum`: the fact must be one of a fixed set of values. `params` is
  `{{"fact": <fact>, "allowed": [<string>, ...]}}`, and `allowed` must be a non-empty
  list of strings (every fact usable here is string-valued -- codes, category names,
  a status word -- never a number). Facts: {enum_facts}.
- `temporal`: a date fact must fall within a window measured from the case's date of
  service. `params` is `{{"fact": <fact>, "operator": <operator>, "value": <days>}}`,
  where `operator` is exactly one of {temporal_operators} and `value` is a positive
  integer number of days. Facts: {temporal_facts}.
- `judgment`: the condition requires interpreting clinical narrative (e.g.
  documented symptoms, a clinician's assessment) that no fact above captures. These
  are decided by reading a member's clinical notes, not by comparing a value -- never
  classify a criterion as `judgment` merely because you are unsure which fact it
  names; if it clearly names one of the facts above, use `threshold`, `enum`, or
  `temporal` instead. `params` is `{{}}` -- no fields.

## fact_args

A few facts cannot be looked up on their own -- they need extra arguments, shown
above as `` `fact` (requires `"fact_args": {{...}}`) ``. Supply them as a nested
object inside `params`. A criterion naming one of those facts without the matching
`fact_args` cannot be checked; a criterion naming any other fact must not include
`fact_args` at all. Read these values from the policy text itself (e.g. "at least 4
hours per night on 70% of nights within a 30-day period" gives `min_hours: 4`,
`window_days: 30`, and the 70% becomes the criterion's own `value`) -- never invent a
number the text does not state.

## Output

Respond with JSON matching the provided schema exactly: `{{"sets": [{{"criteria":
[...]}}]}}`. Emit nothing else.
