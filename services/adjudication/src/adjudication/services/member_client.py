"""Typed wrapper over member's five factual endpoints.

These dataclasses describe another service's wire types, not this service's own
persisted rows -- they stay out of `models/` so nothing suggests a foreign key that
must never exist (member and adjudication each own their own database).

Injected `httpx.AsyncClient` rather than one constructed here, for the same reasons as
`policy_client`: a stub transport in tests, one shared connection pool per case, and no
retries hidden in this layer -- see that module's docstring."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import httpx

from adjudication.services.upstream import UpstreamUnavailable


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


class MemberClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    async def _get(self, path: str, params: dict) -> httpx.Response:
        try:
            response = await self._client.get(f"{self._base_url}{path}", params=params)
        except httpx.TimeoutException as exc:
            raise UpstreamUnavailable("member", "timed out") from exc
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable("member", f"connection failed: {exc}") from exc
        return response

    async def coverage(self, member_id: str, on: date) -> CoverageStatus:
        response = await self._get(f"/members/{member_id}/coverage", {"on": on.isoformat()})

        # The 404 carve-out is specific to this endpoint: here, and only here, a 404
        # is a meaningful answer (no record of this member) rather than a failure.
        if response.status_code == 404:
            return CoverageStatus.NO_RECORD
        if response.status_code // 100 != 2:
            raise UpstreamUnavailable("member", f"status {response.status_code}")

        active = response.json()["active"]
        return CoverageStatus.ACTIVE if active else CoverageStatus.INACTIVE

    async def sleep_studies(self, member_id: str, before: date) -> list[SleepStudy]:
        response = await self._get(
            f"/members/{member_id}/sleep-studies", {"before": before.isoformat()}
        )
        if response.status_code // 100 != 2:
            raise UpstreamUnavailable("member", f"status {response.status_code}")
        # JSON has no date type -- `date.fromisoformat` on the fields the wire model
        # types as `date`, so the dataclass carries real `date` objects, not strings
        # a caller would need to remember to parse.
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
            for study in response.json()
        ]

    async def conditions(self, member_id: str, before: date, codes: list[str]) -> list[Condition]:
        response = await self._get(
            f"/members/{member_id}/conditions",
            {"before": before.isoformat(), "codes": codes},
        )
        if response.status_code // 100 != 2:
            raise UpstreamUnavailable("member", f"status {response.status_code}")
        return [
            Condition(
                id=condition["id"],
                code=condition["code"],
                description=condition["description"],
                onset_date=date.fromisoformat(condition["onset_date"]),
            )
            for condition in response.json()
        ]

    async def adherence(
        self, member_id: str, start: date, end: date, min_hours: float
    ) -> Adherence:
        response = await self._get(
            f"/members/{member_id}/adherence",
            {"start": start.isoformat(), "end": end.isoformat(), "min_hours": min_hours},
        )
        if response.status_code // 100 != 2:
            raise UpstreamUnavailable("member", f"status {response.status_code}")
        return Adherence(**response.json())

    async def notes(self, member_id: str, before: date) -> list[Note]:
        response = await self._get(f"/members/{member_id}/notes", {"before": before.isoformat()})
        if response.status_code // 100 != 2:
            raise UpstreamUnavailable("member", f"status {response.status_code}")
        return [
            Note(
                id=note["id"],
                encounter_id=note["encounter_id"],
                date=date.fromisoformat(note["date"]),
                text=note["text"],
            )
            for note in response.json()
        ]
