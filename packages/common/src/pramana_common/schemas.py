"""Wire models crossing service boundaries.

Both sides of every call import these, so a contract change breaks at import rather than
at runtime. This is the single coupling point between services -- anything only one
service needs stays in that service."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pramana_common.criteria import CriterionType, GateReason, Outcome, Verdict
from pramana_common.gate import GateDecision


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: Where the span came from, as "kind:id" -- for example "note:41" or "chunk:1902".
    source: str
    locator: str
    text: str


class Criterion(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    ordinal: int
    text: str
    type: CriterionType
    #: Values the verifier compares against. The model fills these in; code performs the
    #: comparison. See docs/decisions/0003.
    #: Not deeply frozen: `frozen=True` stops the field being rebound, but the dict's
    #: contents can still be edited in place. Copy before handing it to anything that
    #: might write to it.
    params: dict[str, object] = Field(default_factory=dict)
    #: Required: a criterion that cannot be traced back to policy text cannot be shown to
    #: a reviewer, and an untraceable criterion is one the system should not act on.
    source_chunk_id: int


class CriterionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    criterion_id: str
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    #: Which verifier produced this, for the audit trail: "sql" or "retrieval+llm".
    tool: str
    #: A tuple, not a list: these models are recorded on the audit trail, and a list
    #: field stays appendable even under `frozen=True`.
    evidence: tuple[EvidenceSpan, ...] = Field(default_factory=tuple)


class CaseRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    member_id: str
    requested_code: str
    icd10: str
    date_of_service: date
    #: Continuation cases are adjudicated against the policy's continuation criteria --
    #: adherence and benefit -- rather than its initial criteria.
    kind: Literal["initial", "continuation"] = "initial"


class Determination(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: int
    outcome: Outcome
    #: Tuples, not lists: these travel to the audit trail, and `frozen=True` only stops
    #: the field being rebound -- a list field would still be appendable in place.
    blocking: tuple[str, ...] = Field(default_factory=tuple)
    reason: GateReason | None = None
    criteria: tuple[CriterionOutcome, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _outcome_and_reason_agree(self) -> "Determination":
        # The gate cannot emit an approval that lists blocking criteria, nor an
        # escalation with no reason to show the reviewer. The wire format must not be
        # able to express what the gate cannot produce, or a service could hand-build an
        # approval that never passed the gate and nothing downstream would notice.
        if self.outcome is Outcome.APPROVE:
            if self.blocking:
                raise ValueError("an approval cannot list blocking criteria")
            if self.reason is not None:
                raise ValueError("an approval cannot carry a reason")
        elif self.reason is None:
            raise ValueError("an escalation must carry a reason for the reviewer")
        return self

    @classmethod
    def from_gate_decision(
        cls,
        case_id: int,
        decision: GateDecision,
        criteria: list[CriterionOutcome],
    ) -> "Determination":
        """Build the wire form of a gate decision.

        The single supported path from decision to wire, so no service has to assemble
        the outcome/blocking/reason triple by hand and get the coupling right again."""
        return cls(
            case_id=case_id,
            outcome=decision.outcome,
            blocking=decision.blocking,
            reason=decision.reason,
            criteria=tuple(criteria),
        )
