"""The /cases resource: submit a case (enqueued for the worker, idempotent on a
caller-supplied key) and read one back. Orchestration lives in
`services.intake.submit_case`; this module only validates the request shape and shapes
the response."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from adjudication.models.case import Case
from adjudication.repositories import cases as cases_repo
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
