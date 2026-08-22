"""The golden-case resource: authoring and listing the human-written label set."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pramana_common.criteria import Outcome
from pydantic import BaseModel, Field

from evals.models.golden_case import GoldenCase
from evals.repositories import golden_cases as golden_repo

router = APIRouter()


class GoldenCaseIn(BaseModel):
    #: Forwarded verbatim to adjudication's POST /cases. Not modelled field by field
    #: here: mirroring another service's request schema would be two definitions of one
    #: contract, and they would drift the day adjudication adds a field.
    fixture: dict[str, Any]
    expected_outcome: Outcome
    #: The criteria a person reading the policy expects this case to decompose into.
    #: Optional, because a case can be labelled at the outcome level alone -- but
    #: extraction precision and recall are only measurable for cases that have it.
    expected_criteria: list[str] = Field(default_factory=list)
    #: Required, and required to be a person. A label a model wrote measures agreement
    #: between two models, not correctness (ADR-0009).
    author: str = Field(min_length=1)
    notes: str | None = None


def _to_wire(case: GoldenCase) -> dict:
    return {
        "id": case.id,
        "fixture": case.fixture,
        "expected_outcome": case.expected_outcome.value,
        "expected_criteria": case.expected_criteria,
        "author": case.author,
        "notes": case.notes,
        "created_at": case.created_at.isoformat(),
    }


@router.post("/golden-cases", status_code=201)
async def create(body: GoldenCaseIn, request: Request) -> dict:
    required = {"member_id", "requested_code", "icd10", "date_of_service", "kind"}
    missing = sorted(required - set(body.fixture))
    if missing:
        # Checked here rather than left for the run to discover: a golden case that
        # cannot be submitted is a broken label, and finding that out mid-run means the
        # run reports a gap where there was really a typo.
        raise HTTPException(
            status_code=422, detail=f"fixture is missing required keys: {missing}"
        )

    async with request.app.state.pool.acquire() as conn:
        case = await golden_repo.insert(
            conn,
            fixture=body.fixture,
            expected_outcome=body.expected_outcome,
            expected_criteria=body.expected_criteria,
            author=body.author,
            notes=body.notes,
        )
    return _to_wire(case)


@router.get("/golden-cases")
async def list_cases(request: Request) -> list[dict]:
    async with request.app.state.pool.acquire() as conn:
        cases = await golden_repo.list_all(conn)
    return [_to_wire(case) for case in cases]
