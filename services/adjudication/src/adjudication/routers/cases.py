"""The /cases resource: submit a case (enqueued for the worker, idempotent on a
caller-supplied key), list the queue, read one case back with its criteria and evidence,
and record a clinician's review. Orchestration lives in `services.intake.submit_case`;
this module validates request shapes and shapes responses."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from adjudication.models.case import Case
from adjudication.repositories import cases as cases_repo
from adjudication.repositories import criteria as criteria_repo
from adjudication.repositories import reviews as reviews_repo
from adjudication.services.intake import submit_case

router = APIRouter()


class CaseCreate(BaseModel):
    member_id: str
    requested_code: str
    icd10: str
    date_of_service: date
    kind: Literal["initial", "continuation"]
    #: The clinical narrative, if the caller has one -- retrieval input for the
    #: policy search (migrations/0002_cases_request_text.sql,
    #: services/pipeline.py's fallback to the codes when this is absent).
    request_text: str | None = None
    #: A caller-supplied key that makes a retried POST /cases return the same case
    #: instead of enqueuing -- and therefore adjudicating -- it a second time
    #: (task-8 brief, decision 1). Omitted, this request is not idempotent: every call
    #: creates a new case, which is correct for a caller with no retry concern of its
    #: own.
    idempotency_key: str | None = None


def _case_to_wire(case: Case) -> dict:
    return {
        "id": case.id,
        "member_id": case.member_id,
        "requested_code": case.requested_code,
        "icd10": case.icd10,
        "date_of_service": case.date_of_service.isoformat(),
        "kind": case.kind,
        "status": case.status,
        "created_at": case.created_at.isoformat(),
        "request_text": case.request_text,
    }


@router.post("/cases", status_code=202)
async def create_case(payload: CaseCreate, request: Request) -> dict:
    state = request.app.state
    case, _created = await submit_case(
        state.pool,
        state.redis,
        member_id=payload.member_id,
        requested_code=payload.requested_code,
        icd10=payload.icd10,
        date_of_service=payload.date_of_service,
        kind=payload.kind,
        request_text=payload.request_text,
        idempotency_key=payload.idempotency_key,
    )
    return {"case_id": case.id}


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request) -> dict:
    case = await cases_repo.get(request.app.state.pool, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    return _case_to_wire(case)


#: The closed vocabulary a clinician may record, settled in ADR-0019 and enforced by
#: `reviews_outcome_check` (migrations/0004_reviews_outcome_vocabulary.sql). Three values,
#: not the machine's two: `deny` is the adverse determination the system itself may never
#: issue (ADR-0002), and `pend` is the disposition of a clinician who cannot decide from the
#: record -- without it such a clinician either records nothing, and the case leaves the
#: flywheel with no `agreed_with_system` row at all, or records a decision they did not
#: reach. Partial approval is deliberately absent: a case carries one code, one date and no
#: units, so there is nothing here for a partial to be partial of, and a partial is legally
#: adverse as to the portion refused -- recorded as a fourth flat value it would make "was an
#: adverse determination issued" unanswerable from this column. ADR-0019 states the condition
#: under which that reopens.
ReviewOutcome = Literal["approve", "deny", "pend"]


class ReviewIn(BaseModel):
    """A clinician's own decision on an escalated case.

    `outcome` is a closed set here and a *different* closed set from a determination's, which
    is the point rather than an inconsistency: the machine has two outcomes and a clinician
    has three, and the third is the denial the machine may never issue. See `ReviewOutcome`
    above and ADR-0019 -- this was plan 04's one deliberately-open column and it is now shut.

    `agreed_with_system` is the flywheel: one boolean that turns clinical work into eval
    data. Required, not defaulted -- a default would silently record agreement nobody
    expressed."""

    outcome: ReviewOutcome
    rationale: str = Field(min_length=1)
    agreed_with_system: bool


@router.get("/cases")
async def list_cases(
    request: Request,
    outcome: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """The review queue. Filtered by determination outcome (`escalate` for the work
    queue) or by pipeline status, newest first."""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")

    rows = await cases_repo.list_with_determinations(
        request.app.state.pool, outcome=outcome, status=status, limit=limit
    )
    return [
        {
            **_case_to_wire(row["case"]),
            "determination": (
                None
                if row["outcome"] is None
                else {
                    "outcome": row["outcome"],
                    "reason": row["reason"],
                    "blocking": row["blocking"],
                    "winning_set": row["winning_set"],
                    "decided_at": row["decided_at"].isoformat(),
                }
            ),
        }
        for row in rows
    ]


@router.get("/cases/{case_id}/criteria")
async def list_case_criteria(case_id: str, request: Request) -> dict:
    """Every criterion of the case with its verdict, confidence, tool and evidence,
    grouped into the alternative sets the policy decomposed into (ADR-0011).

    Grouped here rather than left to the client: which criteria belong to one satisfiable
    set is a fact about the policy, and a caller reassembling it from a flat list could
    get it wrong in a way that would misrepresent why a case was refused."""
    pool = request.app.state.pool
    if await cases_repo.get(pool, case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")

    criteria = await criteria_repo.list_for_case_with_results(pool, case_id)

    sets: dict[int, list[dict]] = {}
    for criterion in criteria:
        sets.setdefault(criterion["set_ordinal"], []).append(criterion)

    return {
        "case_id": case_id,
        "sets": [
            {"set_ordinal": ordinal, "criteria": sets[ordinal]} for ordinal in sorted(sets)
        ],
    }


@router.get("/cases/{case_id}/reviews")
async def list_case_reviews(case_id: str, request: Request) -> list[dict]:
    reviews = await reviews_repo.list_for_case(request.app.state.pool, case_id)
    return [
        {
            "id": review.id,
            "clinician_id": review.clinician_id,
            "outcome": review.outcome,
            "rationale": review.rationale,
            "agreed_with_system": review.agreed_with_system,
            "created_at": review.created_at.isoformat(),
        }
        for review in reviews
    ]


@router.post("/cases/{case_id}/review", status_code=201)
async def create_review(case_id: str, payload: ReviewIn, request: Request) -> dict:
    """Record a clinician's decision.

    The clinician is taken from the `X-Pramana-User-Id` header the gateway writes after
    resolving the session -- not from the request body. A body field would let a caller
    attribute their decision to another clinician, and this row is the record of who
    made an adverse determination, which Illinois law is specifically about.
    """
    clinician_id = request.headers.get("x-pramana-user-id", "")
    if not clinician_id:
        raise HTTPException(
            status_code=401,
            detail="no authenticated clinician; this route is reached through the gateway",
        )

    pool = request.app.state.pool
    if await cases_repo.get(pool, case_id) is None:
        raise HTTPException(status_code=404, detail="case not found")

    async with pool.acquire() as conn:
        review = await reviews_repo.insert(
            conn,
            case_id=case_id,
            clinician_id=clinician_id,
            outcome=payload.outcome,
            rationale=payload.rationale,
            agreed_with_system=payload.agreed_with_system,
        )

    return {"id": review.id, "case_id": review.case_id, "created_at": review.created_at.isoformat()}
