# packages/common

**The only code more than one service is allowed to share.** No port, no database.

Pramana is database-per-service and nothing imports across a service boundary — with one
exception, which is this package. It exists because a few concepts must mean *exactly* the same
thing in every service, and the way to guarantee that is for there to be one definition.

Three modules, and the reason each is here rather than in a service:

| module | holds | why it must be shared |
| --- | --- | --- |
| `criteria.py` | the closed vocabularies | if `adjudication` and `evals` disagree about what `escalate` means, every number the harness publishes is wrong |
| `gate.py` | the approval decision | the rule that "no deny path" is enforced by, not merely documented by |
| `schemas.py` | wire shapes crossing service boundaries | a criterion serialised by one service and read by another |

## The vocabularies

```python
CriterionType  = threshold | enum | temporal | judgment
Verdict        = met | not_met | insufficient_evidence
GateReason     = criterion_not_met | insufficient_evidence | low_confidence | no_criteria
Outcome        = approve | escalate          # ← two members. There is no third.
```

`Outcome` having exactly two members is the project's central constraint expressed as a type.
There is no `deny`, no `reject`, no `partial`, and no way for a service to add one — which is
stronger than a code-review convention, because a fifth value would require changing this package
and the database CHECK constraints together.

`DETERMINISTIC_TYPES` names which criterion types are verified by code rather than by a model.
The moment a fifth `CriterionType` appears it must be classified in the same commit, and a test
fails if it is not — an unclassified type would otherwise fall silently to the model, which is
precisely the arrangement this project argues against.

`Verdict` distinguishes `not_met` from `insufficient_evidence`, and that distinction is
load-bearing. "The record contradicts this criterion" and "the record does not answer this
criterion" are different findings for a reviewer, and collapsing them would turn a missing
document into a negative answer.

## The gate

```python
evaluate_gate(criteria, thresholds) -> GateDecision
```

Approve if **every** criterion is `met` **and** every confidence clears the threshold. Anything
else escalates, with the reason naming why and the blocking criteria listed.

That is the whole rule, and its shortness is the point: the safety property "this system cannot
deny" is a function small enough to read in one sitting, in one place, with a test suite over
every combination.

`CriterionResult` validates its own confidence on construction — a value outside `[0, 1]` is
rejected rather than silently clamped, and NaN is rejected explicitly, because a NaN confidence
compares false against every threshold and would pass a "not below the floor" check while meaning
nothing.

## Running the tests

```bash
cd packages/common
uv run pytest        # 45 tests
```

No database, no network, no model. This package is pure logic, which is why it is the one place
where exhaustive testing is cheap.

The suite includes a lexical guard asserting that no member of `Outcome` resembles a denial. It
is deliberately narrow — it would miss a member added as `REFUSE` — and the real coverage comes
from a sibling test asserting set equality against the two permitted values. Both are kept: the
narrow one documents the intent, the strict one enforces it.

## Caveats

- **`pramana_common/__init__.py` is empty**, so consumers import submodule paths
  (`from pramana_common.gate import evaluate_gate`). Deliberate for now, but five services have
  hard-coded that shape, so re-exporting later is a wider change than it looks.
- **`Criterion.params` is a plain dict and is not deeply frozen.** Documented in `schemas.py` and
  deliberately not fixed; decide before a service starts mutating one.
- **`evaluate_gate` and `Determination.from_gate_decision` annotate `list` where `Sequence` would
  be more honest** — both already copy their input, and the eval harness plausibly replays tuples.
