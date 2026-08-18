"""The /seed resource: read the committed Synthea fixture and hand it to
services.seed for insertion."""

import csv
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from member.domain.synthea import parse_conditions, parse_encounters, parse_patients
from member.services.seed import FIXTURE_PLAN, seed_population

router = APIRouter()

# The committed fixture is the only synthetic population source this service ships
# with (until plan 04 needs a larger one) -- read from the same CSVs the test suite
# parses, rather than a second copy, so the two can never drift apart.
_FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "synthea"


def _read_fixture_csv(name: str) -> list[dict]:
    with (_FIXTURE_DIR / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


class SeedIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    seed: int


class SeedOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    members: int
    studies: int
    usage_nights: int
    notes: int


@router.post("/seed")
async def seed(body: SeedIn, request: Request) -> SeedOut:
    """Seeds the committed fixture population, idempotent by member id (see
    member.services.seed). The request only carries `seed` -- which member gets which
    case shape is FIXTURE_PLAN, not caller input, since the fixture's own patient ids
    are the only ones this data could ever apply to."""
    patients = parse_patients(_read_fixture_csv("patients.csv"))
    conditions = parse_conditions(_read_fixture_csv("conditions.csv"))
    encounters = parse_encounters(_read_fixture_csv("encounters.csv"))

    # One transaction for the whole batch: a failure partway through must leave neither
    # a member without its rows nor some members seeded and others silently dropped.
    async with request.app.state.pool.acquire() as conn, conn.transaction():
        result = await seed_population(
            conn, patients, conditions, encounters, body.seed, FIXTURE_PLAN
        )
    return SeedOut.model_validate(result)
