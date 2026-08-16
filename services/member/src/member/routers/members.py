"""The member resource: coverage, sleep studies, conditions, adherence and notes.

None of these return a verdict -- coverage_active answers "was coverage active", not
"is this member eligible" -- that judgment belongs to the adjudication service alone
(ADR-0003). Every date parameter below is typed as `date`, not `str`: FastAPI validates
the query string against that type before the handler runs, so a malformed date 422s
instead of reaching a query that would silently build the wrong window."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from member.repositories import conditions as conditions_repo
from member.repositories import cpap_usage as cpap_usage_repo
from member.repositories import members as members_repo
from member.repositories import notes as notes_repo
from member.repositories import sleep_studies as sleep_studies_repo

router = APIRouter()


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


@router.get("/members/{member_id}/coverage")
async def coverage(member_id: str, on: date, request: Request) -> CoverageOut:
    async with request.app.state.pool.acquire() as conn:
        # coverage_start is non-nullable, so a member row's absence can only mean "no
        # record of this member" -- never "no coverage". coverage_active alone can't
        # tell those apart (it answers False for both), and collapsing them here would
        # let a member missing from the system be treated as a member proven uncovered:
        # a data-availability failure masquerading as a fact that supports denial. The
        # other routes don't need this check -- an empty condition or note list for an
        # unknown member is still a true fact, since absence of conditions is a fact.
        if await members_repo.get(conn, member_id) is None:
            raise HTTPException(status_code=404, detail="member not found")
        active = await members_repo.coverage_active(conn, member_id, on)
    return CoverageOut(active=active)


@router.get("/members/{member_id}/sleep-studies")
async def sleep_studies(member_id: str, before: date, request: Request) -> list[SleepStudyOut]:
    async with request.app.state.pool.acquire() as conn:
        studies = await sleep_studies_repo.sleep_studies_before(conn, member_id, before)
    return [SleepStudyOut.model_validate(study) for study in studies]


@router.get("/members/{member_id}/conditions")
async def conditions(
    member_id: str, before: date, codes: Annotated[list[str], Query()], request: Request
) -> list[ConditionOut]:
    async with request.app.state.pool.acquire() as conn:
        found = await conditions_repo.conditions_before(conn, member_id, before, codes)
    return [ConditionOut.model_validate(condition) for condition in found]


@router.get("/members/{member_id}/adherence")
async def member_adherence(
    member_id: str, start: date, end: date, min_hours: float, request: Request
) -> AdherenceOut:
    """`min_hours` is a required query parameter, like `codes` on /conditions: the
    nightly-hours bar is the caller's policy, not this service's (see
    repositories.cpap_usage.adherence)."""
    async with request.app.state.pool.acquire() as conn:
        result = await cpap_usage_repo.adherence(conn, member_id, start, end, min_hours)
    return AdherenceOut.model_validate(result)


@router.get("/members/{member_id}/notes")
async def notes(member_id: str, before: date, request: Request) -> list[NoteOut]:
    async with request.app.state.pool.acquire() as conn:
        found = await notes_repo.notes_before(conn, member_id, before)
    return [NoteOut.model_validate(note) for note in found]
