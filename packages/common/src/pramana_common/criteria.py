"""The vocabulary every service shares when talking about criteria and outcomes.

Lives in the common package because the adjudication service produces these values, the
eval harness replays them, and the console renders them. A second definition anywhere
would let the three drift apart silently."""

import math
from dataclasses import dataclass
from enum import StrEnum


class CriterionType(StrEnum):
    THRESHOLD = "threshold"
    ENUM = "enum"
    TEMPORAL = "temporal"
    JUDGMENT = "judgment"


#: Types whose verdict is decided by comparing values in code. A model classifies a
#: criterion into one of these and supplies the parameters, but never performs the
#: comparison itself -- see docs/decisions/0003.
DETERMINISTIC_TYPES = frozenset(
    {CriterionType.THRESHOLD, CriterionType.ENUM, CriterionType.TEMPORAL}
)


class Verdict(StrEnum):
    MET = "met"
    NOT_MET = "not_met"
    #: Not an error. This is the most valuable verdict the system produces: it is what
    #: sends a case to a human rather than guessing.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class GateReason(StrEnum):
    """Why the gate declined to approve, as a closed set.

    This value is rendered to the clinician who picks the case up, so a free-text field
    would let a caller put "denied by policy" in front of a reviewer -- a denial in
    everything but the outcome enum. Only these four sentences may be said, and each one
    tells the reviewer what to do next: read the conflicting record, go find the missing
    document, or re-read what the system was unsure about."""

    NO_CRITERIA = "no_criteria"
    CRITERION_NOT_MET = "criterion_not_met"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    LOW_CONFIDENCE = "low_confidence"


class Outcome(StrEnum):
    """Exactly two members, permanently.

    A denial requires a licensed clinician under California SB 1120 and the Medicare
    Advantage rule, so the system has no branch that produces one. Adding a third member
    here would be adding that branch -- see docs/decisions/0002."""

    APPROVE = "approve"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    verdict: Verdict
    confidence: float

    def __post_init__(self) -> None:
        # NaN would compare false against every threshold, quietly passing a check meant
        # to fail. Reject it here rather than letting it reach the gate.
        if math.isnan(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {self.confidence!r}")
