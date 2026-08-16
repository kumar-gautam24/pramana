import csv
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from member.db import SessionFactory, engine
from member.models import Member
from member.queries import (
    adherence,
    conditions_before,
    coverage_active,
    notes_before,
    sleep_studies_before,
)
from member.seed import FIXTURE_PLAN, seed_population
from member.synthea import parse_conditions, parse_encounters, parse_patients

# The committed fixture is the only synthetic population source this service ships
# with (until plan 04 needs a larger one) -- read from the same CSVs the test suite
# parses, rather than a second copy, so the two can never drift apart.
_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "synthea"


def _read_fixture_csv(name: str) -> list[dict]:
    with (_FIXTURE_DIR / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


async def _probe_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail at startup rather than on the first request: create_async_engine opens no
    # connection, so without this a wrong DATABASE_URL starts cleanly and 500s later.
    await _probe_database()
    yield


app = FastAPI(title="pramana member", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is running. Deliberately touches no dependency -- a liveness
    probe that fails on a transient database blip gets the container restarted, which
    fixes nothing."""
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Readiness: this instance can actually serve requests. Reflects the database
    dependency so a caller's circuit breaker routes traffic away from an instance that
    cannot reach it."""
    try:
        await _probe_database()
    except Exception:
        return JSONResponse({"status": "unready", "reason": "database"}, status_code=503)
    return JSONResponse({"status": "ready"})


# Wire models for the routes below. Deliberately member-service-local rather than in
# packages/common: these describe facts, not the adjudication contract, and adding them
# there would give the coupling point a reason to change every time a query's shape does.


class CoverageOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    active: bool


class SleepStudyOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    date: date
    test_type: str
    channels: int
    apnea_events: int
    recorded_hours: float
    ahi: float


class ConditionOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    code: str
    description: str
    onset_date: date


class AdherenceOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    nights: int
    qualifying_nights: int
    fraction: float


class NoteOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: int
    encounter_id: int | None
    date: date
    text: str


class SeedIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    seed: int


class SeedOut(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    members: int
    studies: int
    usage_nights: int
    notes: int


# Every route below takes its date parameters typed as `date`, not `str`: FastAPI
# validates the query string against that type before the handler runs, so a malformed
# date 422s instead of reaching a query that would silently build the wrong window.
# None of these return a verdict -- coverage_active answers "was coverage active", not
# "is this member eligible" -- that judgment belongs to the adjudication service alone
# (ADR-0003).


@app.get("/members/{member_id}/coverage")
async def coverage(member_id: str, on: date) -> CoverageOut:
    async with SessionFactory() as session:
        # coverage_start is non-nullable, so a member row's absence can only mean "no
        # record of this member" -- never "no coverage". coverage_active alone can't
        # tell those apart (it answers False for both), and collapsing them here would
        # let a member missing from the system be treated as a member proven uncovered:
        # a data-availability failure masquerading as a fact that supports denial. The
        # other routes don't need this check -- an empty condition or note list for an
        # unknown member is still a true fact, since absence of conditions is a fact.
        if await session.get(Member, member_id) is None:
            raise HTTPException(status_code=404, detail="member not found")
        active = await coverage_active(session, member_id, on)
    return CoverageOut(active=active)


@app.get("/members/{member_id}/sleep-studies")
async def sleep_studies(member_id: str, before: date) -> list[SleepStudyOut]:
    async with SessionFactory() as session:
        studies = await sleep_studies_before(session, member_id, before)
    return [SleepStudyOut.model_validate(study) for study in studies]


@app.get("/members/{member_id}/conditions")
async def conditions(
    member_id: str, before: date, codes: Annotated[list[str], Query()]
) -> list[ConditionOut]:
    async with SessionFactory() as session:
        found = await conditions_before(session, member_id, before, codes)
    return [ConditionOut.model_validate(condition) for condition in found]


@app.get("/members/{member_id}/adherence")
async def member_adherence(member_id: str, start: date, end: date) -> AdherenceOut:
    async with SessionFactory() as session:
        result = await adherence(session, member_id, start, end)
    return AdherenceOut.model_validate(result)


@app.get("/members/{member_id}/notes")
async def notes(member_id: str, before: date) -> list[NoteOut]:
    async with SessionFactory() as session:
        found = await notes_before(session, member_id, before)
    return [NoteOut.model_validate(note) for note in found]


@app.post("/seed")
async def seed(body: SeedIn) -> SeedOut:
    """Seeds the committed fixture population, idempotent by member id (see
    member.seed). The request only carries `seed` -- which member gets which case
    shape is FIXTURE_PLAN, not caller input, since the fixture's three patient ids are
    the only ones this data could ever apply to."""
    patients = parse_patients(_read_fixture_csv("patients.csv"))
    conditions = parse_conditions(_read_fixture_csv("conditions.csv"))
    encounters = parse_encounters(_read_fixture_csv("encounters.csv"))

    async with SessionFactory() as session:
        result = await seed_population(
            session, patients, conditions, encounters, body.seed, FIXTURE_PLAN
        )
        await session.commit()
    return SeedOut.model_validate(result)
