"""Decides whether a prior-authorization request may be approved without a human.

Lives in the common package because two callers need identical semantics: the
adjudication service applies it per case, and the threshold sweep replays it over
recorded criterion results. A second implementation in the sweep would be measuring
something other than what production does.

The gate is asymmetric by design. It can approve, or it can decline to decide. It cannot
deny -- see docs/decisions/0002-no-deny-path.md."""

import math
from dataclasses import dataclass

from pramana_common.criteria import CriterionResult, Outcome, Verdict


@dataclass(frozen=True)
class GateThresholds:
    min_confidence: float = 0.0

    def __post_init__(self) -> None:
        # Eval runs persist thresholds as JSON so a run can be reproduced, and JSON has
        # no infinity. Rejecting here gives a clear error instead of a failure at insert.
        if math.isnan(self.min_confidence) or not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError(
                f"min_confidence must be a finite value within [0, 1], "
                f"got {self.min_confidence!r}"
            )


@dataclass(frozen=True)
class GateDecision:
    outcome: Outcome
    #: Criterion ids that prevented approval, in the order they were evaluated, so the
    #: reviewer sees them in the order the policy states them.
    blocking: tuple[str, ...]
    reason: str | None


def _blocks(result: CriterionResult, thresholds: GateThresholds) -> bool:
    return result.verdict is not Verdict.MET or result.confidence < thresholds.min_confidence


def evaluate_gate(
    results: list[CriterionResult], thresholds: GateThresholds
) -> GateDecision:
    """Approve only when every criterion is met with sufficient confidence.

    Reason ordering is deliberate. NOT_MET means the record contradicts the policy and
    the reviewer may be heading towards a denial; INSUFFICIENT_EVIDENCE means a document
    is missing and someone should go find it; LOW_CONFIDENCE means the system read the
    record but is not sure. The first is the most actionable, so it is reported first.
    """
    if not results:
        # No criteria means decomposition failed to understand the policy. It never means
        # a policy with no requirements, so it must not fall through to approval.
        return GateDecision(Outcome.ESCALATE, (), "no_criteria")

    blocking = tuple(r.criterion_id for r in results if _blocks(r, thresholds))
    if not blocking:
        return GateDecision(Outcome.APPROVE, (), None)

    blocked = [r for r in results if _blocks(r, thresholds)]
    if any(r.verdict is Verdict.NOT_MET for r in blocked):
        reason = "criterion_not_met"
    elif any(r.verdict is Verdict.INSUFFICIENT_EVIDENCE for r in blocked):
        reason = "insufficient_evidence"
    else:
        reason = "low_confidence"

    return GateDecision(Outcome.ESCALATE, blocking, reason)
