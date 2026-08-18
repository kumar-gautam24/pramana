"""Deciding a case across its alternative criteria sets -- see ADR-0011.

A policy that has been decomposed into disjunctive criteria sets is satisfied if any
one set is fully met. `evaluate_gate` already decides whether a *single* set of results
permits approval; this module runs it once per set and picks among the per-set answers.
It contains no confidence comparison or verdict check of its own -- that logic already
exists inside `evaluate_gate` and is not re-derived here.

Pure: no I/O, no imports of `db`, `asyncpg`, `httpx`, or `repositories/`."""

from dataclasses import dataclass

from pramana_common.criteria import CriterionResult, GateReason, Outcome
from pramana_common.gate import GateDecision, GateThresholds, evaluate_gate

from adjudication.models.criterion import Criterion


class MissingCriterionResult(LookupError):
    """A set references a criterion with no entry in `results`.

    Raised rather than treated as unverified evidence, and distinct from a bare
    KeyError so it cannot be mistaken for a bug in the caller's dict: a verification
    that silently vanished must never round down to an approval."""

    def __init__(self, criterion_id: int, set_ordinal: int) -> None:
        super().__init__(
            f"criterion {criterion_id} in set {set_ordinal} has no result in `results`"
        )
        self.criterion_id = criterion_id
        self.set_ordinal = set_ordinal


@dataclass(frozen=True)
class CriteriaSet:
    ordinal: int
    criteria: tuple[Criterion, ...]


@dataclass(frozen=True)
class SetOutcome:
    ordinal: int
    decision: GateDecision
    #: Exactly `decision.blocking` -- every criterion the gate declined to approve on,
    #: not only the ones verdicted NOT_MET. A set with five insufficient-evidence
    #: criteria is further from approval than a set with one not-met criterion, so
    #: "unmet" must count everything that blocked, or `aggregate`'s "closest set" pick
    #: (rule 3) would send the reviewer to the wrong set. Built from `decision.blocking`
    #: directly rather than a second filter pass over the results, so this can never
    #: drift from what the gate actually decided.
    unmet: tuple[str, ...]


@dataclass(frozen=True)
class AggregateDecision:
    outcome: Outcome
    reason: GateReason | None
    winning_set: int | None
    blocking: tuple[str, ...]
    closest_set: int | None


def _evaluate_set(
    criteria_set: CriteriaSet,
    results: dict[str, CriterionResult],
    thresholds: GateThresholds,
) -> SetOutcome:
    set_results = []
    for criterion in criteria_set.criteria:
        # Criterion.id is an int (a bigserial primary key). CriterionResult.criterion_id
        # is a str, because packages/common owns that type and the eval harness replays
        # recorded criterion results whose ids never came from this database. This is
        # the one place the two types meet; nothing else in this module converts.
        key = str(criterion.id)
        try:
            set_results.append(results[key])
        except KeyError:
            raise MissingCriterionResult(criterion.id, criteria_set.ordinal) from None

    decision = evaluate_gate(set_results, thresholds)
    return SetOutcome(ordinal=criteria_set.ordinal, decision=decision, unmet=decision.blocking)


def aggregate(
    sets: list[CriteriaSet],
    results: dict[str, CriterionResult],
    thresholds: GateThresholds,
) -> AggregateDecision:
    """Decide a case across its alternative criteria sets.

    Raises `MissingCriterionResult` if any set references a criterion absent from
    `results`."""
    if not sets:
        # No sets means extraction failed to understand the policy -- the same
        # reasoning evaluate_gate applies to an empty result list, one level up. It
        # never means a policy with no requirements, so it must not fall through to
        # approval.
        return AggregateDecision(
            outcome=Outcome.ESCALATE,
            reason=GateReason.NO_CRITERIA,
            winning_set=None,
            blocking=(),
            closest_set=None,
        )

    outcomes = [_evaluate_set(s, results, thresholds) for s in sets]

    approving = [o for o in outcomes if o.decision.outcome is Outcome.APPROVE]
    if approving:
        # Ties broken by lowest ordinal so the same case adjudicated twice names the
        # same set, regardless of dict or list iteration order -- the same
        # audit-reproducibility argument as `in_force_on`'s tiebreak.
        winner = min(approving, key=lambda o: o.ordinal)
        return AggregateDecision(
            outcome=Outcome.APPROVE,
            reason=None,
            winning_set=winner.ordinal,
            blocking=(),
            closest_set=None,
        )

    # None approved: escalate, naming the closest set -- fewest unmet criteria, ties
    # broken by lowest ordinal for the same reproducibility reason as above. A set with
    # no criteria reports zero unmet too (evaluate_gate's NO_CRITERIA case, blocking =
    # ()), but zero unmet there means unsatisfiable, not nearly satisfied -- an empty
    # set must never look closer to approval than a real set with an actual unmet
    # criterion. Ranking NO_CRITERIA sets last (False < True) fixes that; if every set
    # is empty the pick still lands on one and correctly reports NO_CRITERIA.
    closest = min(
        outcomes,
        key=lambda o: (o.decision.reason is GateReason.NO_CRITERIA, len(o.unmet), o.ordinal),
    )
    return AggregateDecision(
        outcome=Outcome.ESCALATE,
        reason=closest.decision.reason,
        winning_set=None,
        blocking=closest.unmet,
        closest_set=closest.ordinal,
    )
