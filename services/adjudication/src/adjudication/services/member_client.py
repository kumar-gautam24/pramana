"""Typed wrapper over member's five factual endpoints.

These dataclasses describe another service's wire types, not this service's own
persisted rows -- they stay out of `models/` so nothing suggests a foreign key that
must never exist (member and adjudication each own their own database).

Injected `httpx.AsyncClient` rather than one constructed here, for the same reasons as
`policy_client`: a stub transport in tests, one shared connection pool per case, and no
retries hidden in this layer -- see that module's docstring, and `worker.py` for where a
retry does live."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import httpx

from adjudication.services import upstream


class CoverageStatus(StrEnum):
    """Tri-state, never a bool. `member`'s /coverage endpoint distinguishes "no record
    of this member" (404) from "a record exists and coverage was not active" (200,
    active: false) -- see the comment in member/routers/members.py's coverage handler.
    Collapsing those two into a bool would let a member missing from the system read as
    a member proven uncovered: a data-availability failure masquerading as a fact that
    supports an escalation-worthy denial path. A three-member enum makes that collapse
    impossible to express, not merely discouraged."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    NO_RECORD = "no_record"


@dataclass(frozen=True)
class SleepStudy:
    id: int
    date: date
    test_type: str
    channels: int
    apnea_events: int
    recorded_hours: float
    ahi: float


@dataclass(frozen=True)
class Condition:
    id: int
    code: str
    description: str
    onset_date: date


@dataclass(frozen=True)
class Adherence:
    nights: int
    qualifying_nights: int
    fraction: float


@dataclass(frozen=True)
class Note:
    id: int
    encounter_id: int | None
    date: date
    text: str


def _build_sleep_studies(body: object) -> list[SleepStudy]:
    # JSON has no date type -- `date.fromisoformat` on the fields the wire model types
    # as `date`, so the dataclass carries real `date` objects, not strings a caller
    # would need to remember to parse.
    return [
        SleepStudy(
            id=study["id"],
            date=date.fromisoformat(study["date"]),
            test_type=study["test_type"],
            channels=study["channels"],
            apnea_events=study["apnea_events"],
            recorded_hours=study["recorded_hours"],
            ahi=study["ahi"],
        )
        for study in body
    ]


def _build_conditions(body: object) -> list[Condition]:
    return [
        Condition(
            id=condition["id"],
            code=condition["code"],
            description=condition["description"],
            onset_date=date.fromisoformat(condition["onset_date"]),
        )
        for condition in body
    ]


def _build_notes(body: object) -> list[Note]:
    return [
        Note(
            id=note["id"],
            encounter_id=note["encounter_id"],
            date=date.fromisoformat(note["date"]),
            text=note["text"],
        )
        for note in body
    ]


class MemberClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    async def coverage(self, member_id: str, on: date) -> CoverageStatus:
        path = f"/members/{member_id}/coverage"
        response = await upstream.send(
            self._client, "member", "GET", f"{self._base_url}{path}", params={"on": on.isoformat()}
        )

        # The 404 carve-out is specific to this endpoint, and must be checked before
        # `upstream.parse` below: here, and only here, a 404 is a meaningful answer
        # (no record of this member) rather than a failure `parse` would raise on.
        if response.status_code == 404:
            return CoverageStatus.NO_RECORD

        active = upstream.parse("member", response, path, lambda body: body["active"])
        return CoverageStatus.ACTIVE if active else CoverageStatus.INACTIVE

    async def sleep_studies(self, member_id: str, before: date) -> list[SleepStudy]:
        path = f"/members/{member_id}/sleep-studies"
        response = await upstream.send(
            self._client,
            "member",
            "GET",
            f"{self._base_url}{path}",
            params={"before": before.isoformat()},
        )
        return upstream.parse("member", response, path, _build_sleep_studies)

    async def conditions(self, member_id: str, before: date, codes: list[str]) -> list[Condition]:
        path = f"/members/{member_id}/conditions"
        response = await upstream.send(
            self._client,
            "member",
            "GET",
            f"{self._base_url}{path}",
            params={"before": before.isoformat(), "codes": codes},
        )
        return upstream.parse("member", response, path, _build_conditions)

    async def adherence(
        self, member_id: str, start: date, end: date, min_hours: float
    ) -> Adherence:
        path = f"/members/{member_id}/adherence"
        response = await upstream.send(
            self._client,
            "member",
            "GET",
            f"{self._base_url}{path}",
            params={"start": start.isoformat(), "end": end.isoformat(), "min_hours": min_hours},
        )
        return upstream.parse("member", response, path, lambda body: Adherence(**body))

    async def notes(self, member_id: str, before: date) -> list[Note]:
        path = f"/members/{member_id}/notes"
        response = await upstream.send(
            self._client,
            "member",
            "GET",
            f"{self._base_url}{path}",
            params={"before": before.isoformat()},
        )
        return upstream.parse("member", response, path, _build_notes)
