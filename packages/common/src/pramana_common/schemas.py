"""Wire models crossing service boundaries.

Both sides of every call import these, so a contract change breaks at import rather than
at runtime. This is the single coupling point between services -- anything only one
service needs stays in that service."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pramana_common.criteria import CriterionType, Outcome, Verdict


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
    evidence: list[EvidenceSpan] = Field(default_factory=list)


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
    blocking: list[str] = Field(default_factory=list)
    reason: str | None = None
    criteria: list[CriterionOutcome] = Field(default_factory=list)
