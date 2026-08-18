"""Tests for the upstream clients -- see the task-4 brief.

Stub transport only (`httpx.MockTransport`): no live service, no network. Each client
gets its own `httpx.AsyncClient` built with a finite timeout, per the rule that this
layer must never construct one with `timeout=None`."""

import json
from datetime import date

import httpx
import pytest

from adjudication.services.member_client import (
    Adherence,
    Condition,
    CoverageStatus,
    MemberClient,
    Note,
    SleepStudy,
)
from adjudication.services.policy_client import PolicyClient
from adjudication.services.upstream import UpstreamUnavailable

BASE_URL = "http://testserver"


def _client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=5.0)


# --- policy_client -----------------------------------------------------------------


async def test_search_parses_hits():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "chunk_id": 58,
                    "policy_id": 1,
                    "display_id": "240.4",
                    "heading_path": "Indications and Limitations of Coverage > B",
                    "text": "greater than or equal to 15 events per hour",
                    "score": 7.0,
                }
            ],
        )

    async with _client(handler) as client:
        hits = await PolicyClient(client, BASE_URL).search("AHI threshold", date(2026, 1, 15), 5)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.chunk_id == 58
    assert hit.display_id == "240.4"
    assert hit.score == 7.0


async def test_search_sends_date_of_service_and_limit_in_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=[])

    async with _client(handler) as client:
        await PolicyClient(client, BASE_URL).search("AHI threshold", date(2026, 1, 15), 3)

    assert captured["body"] == {
        "query": "AHI threshold",
        "date_of_service": "2026-01-15",
        "limit": 3,
    }


async def test_search_sends_null_date_of_service_when_none():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=[])

    async with _client(handler) as client:
        await PolicyClient(client, BASE_URL).search("AHI threshold", None, 5)

    assert captured["body"]["date_of_service"] is None


async def test_search_500_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable) as exc_info:
            await PolicyClient(client, BASE_URL).search("q", None, 5)

    assert exc_info.value.service == "policy"
    assert "500" in exc_info.value.detail


async def test_search_timeout_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable) as exc_info:
            await PolicyClient(client, BASE_URL).search("q", None, 5)

    assert exc_info.value.service == "policy"


# --- member_client: coverage tri-state ----------------------------------------------


async def test_coverage_active_true_is_active():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"active": True})

    async with _client(handler) as client:
        status = await MemberClient(client, BASE_URL).coverage("p1", date(2026, 1, 15))

    assert status == CoverageStatus.ACTIVE


async def test_coverage_active_false_is_inactive_not_no_record():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"active": False})

    async with _client(handler) as client:
        status = await MemberClient(client, BASE_URL).coverage("p1", date(2026, 1, 15))

    assert status == CoverageStatus.INACTIVE
    assert status != CoverageStatus.NO_RECORD


async def test_coverage_404_is_no_record_not_inactive():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "member not found"})

    async with _client(handler) as client:
        status = await MemberClient(client, BASE_URL).coverage("ghost", date(2026, 1, 15))

    assert status == CoverageStatus.NO_RECORD
    assert status != CoverageStatus.INACTIVE


async def test_coverage_500_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable):
            await MemberClient(client, BASE_URL).coverage("p1", date(2026, 1, 15))


async def test_coverage_timeout_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable):
            await MemberClient(client, BASE_URL).coverage("p1", date(2026, 1, 15))


# --- member_client: 404 carve-out is specific to coverage ---------------------------


async def test_sleep_studies_404_raises_upstream_unavailable():
    """Unlike coverage, a 404 here is not a meaningful answer -- it must escalate, not
    silently return an empty list that would read as "member has no sleep studies"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable):
            await MemberClient(client, BASE_URL).sleep_studies("p1", date(2026, 1, 15))


# --- member_client: the other four factual endpoints ---------------------------------


async def test_sleep_studies_parses_success_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "date": "2025-11-01",
                    "test_type": "home_type_iv",
                    "channels": 4,
                    "apnea_events": 251,
                    "recorded_hours": 5.35,
                    "ahi": 46.916,
                }
            ],
        )

    async with _client(handler) as client:
        studies = await MemberClient(client, BASE_URL).sleep_studies("p1", date(2026, 1, 15))

    assert studies == [
        SleepStudy(
            id=1,
            date=date(2025, 11, 1),
            test_type="home_type_iv",
            channels=4,
            apnea_events=251,
            recorded_hours=5.35,
            ahi=46.916,
        )
    ]


async def test_conditions_parses_success_response_and_sends_repeated_codes():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["codes"] = request.url.params.get_list("codes")
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "code": "59621000",
                    "description": "hypertension",
                    "onset_date": "2020-01-01",
                }
            ],
        )

    async with _client(handler) as client:
        conditions = await MemberClient(client, BASE_URL).conditions(
            "p1", date(2026, 1, 15), ["59621000", "53741008"]
        )

    assert captured["codes"] == ["59621000", "53741008"]
    assert conditions == [
        Condition(id=1, code="59621000", description="hypertension", onset_date=date(2020, 1, 1))
    ]


async def test_conditions_500_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable):
            await MemberClient(client, BASE_URL).conditions("p1", date(2026, 1, 15), ["59621000"])


async def test_adherence_parses_success_response_and_sends_min_hours():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["min_hours"] = request.url.params.get("min_hours")
        return httpx.Response(200, json={"nights": 30, "qualifying_nights": 24, "fraction": 0.8})

    async with _client(handler) as client:
        result = await MemberClient(client, BASE_URL).adherence(
            "p1", date(2026, 1, 1), date(2026, 1, 31), min_hours=4.0
        )

    assert captured["min_hours"] == "4.0"
    assert result == Adherence(nights=30, qualifying_nights=24, fraction=0.8)


async def test_adherence_timeout_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable):
            await MemberClient(client, BASE_URL).adherence(
                "p1", date(2026, 1, 1), date(2026, 1, 31), min_hours=4.0
            )


async def test_notes_parses_success_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": 1, "encounter_id": 41, "date": "2025-11-01", "text": "initial visit"},
                {"id": 2, "encounter_id": None, "date": "2025-12-01", "text": "follow-up"},
            ],
        )

    async with _client(handler) as client:
        notes = await MemberClient(client, BASE_URL).notes("p1", date(2026, 1, 15))

    assert notes == [
        Note(id=1, encounter_id=41, date=date(2025, 11, 1), text="initial visit"),
        Note(id=2, encounter_id=None, date=date(2025, 12, 1), text="follow-up"),
    ]


async def test_notes_500_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable):
            await MemberClient(client, BASE_URL).notes("p1", date(2026, 1, 15))
