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


#: Keys a fixture must carry, checked at authoring time rather than left for the run to
#: discover: a golden case that cannot be submitted is a broken label, and finding that out
#: mid-run means the run reports a gap where there was really a typo.
_REQUIRED_FIXTURE_KEYS = {"member_id", "requested_code", "icd10", "date_of_service", "kind"}

#: Keys a fixture must **not** carry, each because it belongs to the run rather than to the
#: label, and a fixture holding one would make a run's own record of itself untrue.
#:
#: `run_mode` is the run's ablation (`runner._RUN_MODE_FOR_ABLATION`). A fixture pinning it
#: would let a run whose `ablation` column says `model_arithmetic` adjudicate a case the
#: ordinary way, publishing a figure that measures the opposite of its label.
#:
#: `idempotency_key` is worse, because it looks harmless. Adjudication's `POST /cases` is
#: idempotent on that key: a second run submitting the same fixture would be handed the
#: *first* run's case, complete with the first run's determination and run mode. A run and
#: its ablated twin would then score the identical case twice and read as agreeing perfectly.
_FORBIDDEN_FIXTURE_KEYS = {"run_mode", "idempotency_key"}


@router.post("/golden-cases", status_code=201)
async def create(body: GoldenCaseIn, request: Request) -> dict:
    missing = sorted(_REQUIRED_FIXTURE_KEYS - set(body.fixture))
    if missing:
        raise HTTPException(
            status_code=422, detail=f"fixture is missing required keys: {missing}"
        )

    forbidden = sorted(_FORBIDDEN_FIXTURE_KEYS & set(body.fixture))
    if forbidden:
        raise HTTPException(
            status_code=422,
            detail=(
                f"fixture must not set {forbidden}: those belong to the run that submits "
                "this case, not to the label"
            ),
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
