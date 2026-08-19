"""Tests for the HTTP surface: `POST /cases`, `GET /cases/{id}`, `GET
/cases/{id}/events` and `GET /cases/{id}/stream`.

`app.state.pool`/`app.state.redis` are set directly rather than by running the app's
own lifespan: the lifespan's startup probes (Redis, policy, member, the ADR-0010 model
guard) are `test_health.py`'s concern, not this file's, and re-running them here would
only slow every test down without proving anything new. `db_pool` and `redis_client`
(see conftest.py) are the same real Postgres and Redis those probes would have checked
anyway.

A real `uvicorn` server on an ephemeral port, not `httpx.ASGITransport`: `ASGITransport`
buffers an ASGI call's entire response body and only returns once the app coroutine
itself returns (see `httpx._transports.asgi.ASGITransport.handle_async_request`) --
fine for an ordinary JSON response, fatal for `/stream`, whose generator does not
return until the client disconnects. Awaiting it through `ASGITransport` deadlocks: the
client never gets anything back because the handler never finishes producing it. A real
socket streams incrementally the way an actual browser's `EventSource` would, and
running the server as a background task on this test's own event loop (rather than
`TestClient`'s separate thread-and-loop) keeps `app.state.redis`/`app.state.pool` --
constructed on this loop -- valid for the handlers that use them.

Stub `PolicyClient`, `MemberClient` and `LLMProvider` -- no live upstream, no live
model, mirroring `test_pipeline.py`'s own doubles and its `HITS`/`RAW_RESPONSE`
fixture (NCD 240.4's real disjunctive structure)."""

import asyncio
import json
from datetime import date
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fixtures.ncd_240_4_extraction import HITS, RAW_RESPONSE
from pramana_common.gate import GateThresholds

from adjudication.main import app
from adjudication.repositories import case_events as case_events_repo
from adjudication.repositories import cases as cases_repo
from adjudication.services import queue
from adjudication.services.member_client import Adherence, CoverageStatus, SleepStudy
from adjudication.services.pipeline import adjudicate

MEMBER_ID = "m-1"
DATE_OF_SERVICE = "2026-01-15"


# --- test doubles (mirroring test_pipeline.py's) ------------------------------------


class StubPolicyClient:
    def __init__(self, hits=None):
        self._hits = [] if hits is None else hits

    async def search(self, query, date_of_service, limit):
        return self._hits


class StubMemberClient:
    def __init__(self, *, sleep_studies=None, coverage=None):
        self._sleep_studies = [] if sleep_studies is None else sleep_studies
        self._coverage = CoverageStatus.ACTIVE if coverage is None else coverage

    async def coverage(self, member_id, on):
        return self._coverage

    async def sleep_studies(self, member_id, before):
        return self._sleep_studies

    async def conditions(self, member_id, before, codes):
        return []

    async def adherence(self, member_id, start, end, min_hours):
        return Adherence(nights=0, qualifying_nights=0, fraction=0.0)

    async def notes(self, member_id, before):
        return []


class StubLLM:
    def __init__(self, response):
        self.response = response

    async def chat(self, messages, schema):
        return self.response


def _study(**overrides) -> SleepStudy:
    fields = dict(
        id=1,
        date=date(2026, 1, 10),
        test_type="home_type_ii",
        channels=4,
        apnea_events=90,
        recorded_hours=6.0,
        ahi=20.0,
    )
    fields.update(overrides)
    return SleepStudy(**fields)


def _payload(**overrides) -> dict:
    base = dict(
        member_id=MEMBER_ID,
        requested_code="95810",
        icd10="G47.33",
        date_of_service=DATE_OF_SERVICE,
        kind="initial",
    )
    base.update(overrides)
    return base


async def _count_in_stream(redis_client, case_id: str, *, stream: str = queue.STREAM) -> int:
    """How many entries on the (shared, never-cleared) production stream name a
    `case_id` appears in -- robust to whatever earlier test runs have already left on
    this dev Redis, since real case ids are uuid4s and never collide across runs."""
    entries = await redis_client.xrange(stream)
    return sum(1 for _id, fields in entries if fields.get("case_id") == case_id)


@pytest.fixture
async def client(db_pool, redis_client):
    """A real client against a real `uvicorn` server on an ephemeral port -- see the
    module docstring for why `ASGITransport` cannot serve `/stream`. `lifespan="off"`:
    the startup probes are `test_health.py`'s concern, so `app.state.pool`/`.redis` are
    assigned directly from the same fixtures the rest of this suite already trusts."""
    app.state.pool = db_pool
    app.state.redis = redis_client

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as ac:
        yield ac

    server.should_exit = True
    await server_task


# === POST /cases =====================================================================


async def test_post_cases_returns_202_with_a_case_id(client):
    response = await client.post("/cases", json=_payload())

    assert response.status_code == 202
    body = response.json()
    assert "case_id" in body and body["case_id"]


async def test_post_cases_persists_a_queued_case(client, db_pool):
    response = await client.post("/cases", json=_payload())
    case_id = response.json()["case_id"]

    stored = await cases_repo.get(db_pool, case_id)
    assert stored is not None
    assert stored.status == "queued"
    assert stored.member_id == MEMBER_ID


async def test_post_cases_accepts_and_stores_request_text(client):
    narrative = "CPAP for severe obstructive sleep apnea"
    response = await client.post("/cases", json=_payload(request_text=narrative))
    case_id = response.json()["case_id"]

    get_response = await client.get(f"/cases/{case_id}")
    assert get_response.json()["request_text"] == narrative


async def test_post_cases_without_request_text_stores_none(client):
    response = await client.post("/cases", json=_payload())
    case_id = response.json()["case_id"]

    get_response = await client.get(f"/cases/{case_id}")
    assert get_response.json()["request_text"] is None


async def test_post_cases_enqueues_the_case_exactly_once(client, redis_client):
    response = await client.post("/cases", json=_payload())
    case_id = response.json()["case_id"]

    assert await _count_in_stream(redis_client, case_id) == 1


async def test_post_cases_without_an_idempotency_key_creates_two_distinct_cases(client):
    first = await client.post("/cases", json=_payload())
    second = await client.post("/cases", json=_payload())

    assert first.json()["case_id"] != second.json()["case_id"]


async def test_post_cases_with_the_same_idempotency_key_returns_the_same_case(client):
    key = str(uuid4())

    first = await client.post("/cases", json=_payload(idempotency_key=key))
    second = await client.post("/cases", json=_payload(idempotency_key=key))

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["case_id"] == second.json()["case_id"]


async def test_post_cases_with_the_same_idempotency_key_enqueues_only_once(
    client, redis_client
):
    """The point of decision 1: a retried submission must not adjudicate twice, and
    the observable proof of that is the stream, not just the returned case_id."""
    key = str(uuid4())

    first = await client.post("/cases", json=_payload(idempotency_key=key))
    await client.post("/cases", json=_payload(idempotency_key=key))
    case_id = first.json()["case_id"]

    assert await _count_in_stream(redis_client, case_id) == 1


async def test_post_cases_with_different_idempotency_keys_creates_two_cases(client):
    first = await client.post("/cases", json=_payload(idempotency_key=str(uuid4())))
    second = await client.post("/cases", json=_payload(idempotency_key=str(uuid4())))

    assert first.json()["case_id"] != second.json()["case_id"]


# === GET /cases/{id} ==================================================================


async def test_get_case_returns_404_for_an_unknown_id(client):
    response = await client.get(f"/cases/{uuid4()}")

    assert response.status_code == 404


async def test_get_case_returns_the_persisted_fields(client):
    create_response = await client.post("/cases", json=_payload())
    case_id = create_response.json()["case_id"]

    response = await client.get(f"/cases/{case_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == case_id
    assert body["member_id"] == MEMBER_ID
    assert body["requested_code"] == "95810"
    assert body["icd10"] == "G47.33"
    assert body["date_of_service"] == DATE_OF_SERVICE
    assert body["kind"] == "initial"
    assert body["status"] == "queued"


# === GET /cases/{id}/events ===========================================================


async def test_get_events_returns_404_for_an_unknown_case(client):
    response = await client.get(f"/cases/{uuid4()}/events")

    assert response.status_code == 404


async def test_get_events_is_empty_before_anything_has_run(client):
    create_response = await client.post("/cases", json=_payload())
    case_id = create_response.json()["case_id"]

    response = await client.get(f"/cases/{case_id}/events")

    assert response.status_code == 200
    assert response.json() == []


async def test_get_events_reflects_the_stored_log_after_adjudication(client, db_pool):
    case = await cases_repo.insert(
        db_pool,
        member_id=MEMBER_ID,
        requested_code="95810",
        icd10="G47.33",
        date_of_service=date(2026, 1, 15),
        kind="initial",
    )
    await adjudicate(
        case.id,
        db_pool,
        StubPolicyClient(hits=HITS),
        StubMemberClient(sleep_studies=[_study(ahi=20.0)]),
        StubLLM(RAW_RESPONSE),
        GateThresholds(),
    )

    response = await client.get(f"/cases/{case.id}/events")

    events = response.json()
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[0]["type"] == "started"
    assert events[-1]["type"] == "decision"
    assert events[-1]["payload"]["outcome"] == "approve"


# === GET /cases/{id}/stream ============================================================


async def test_stream_returns_404_for_an_unknown_case(client):
    async with client.stream("GET", f"/cases/{uuid4()}/stream") as response:
        assert response.status_code == 404


async def test_stream_and_stored_log_render_the_same_seq_ordering(
    client, db_pool, redis_client, monkeypatch
):
    """The brief's own audit-trail claim: "the SSE stream and the stored log must
    render the same sequence." This runs a real adjudication while a real SSE
    connection is open, and compares what came over the wire, live, against what
    `GET /cases/{id}/events` replays afterwards from `case_events` -- both are read
    through the same HTTP surface a real client would use."""
    monkeypatch.setattr(case_events_repo, "_redis", redis_client)

    case = await cases_repo.insert(
        db_pool,
        member_id=MEMBER_ID,
        requested_code="95810",
        icd10="G47.33",
        date_of_service=date(2026, 1, 15),
        kind="initial",
    )

    async with client.stream("GET", f"/cases/{case.id}/stream") as response:
        assert response.status_code == 200

        async def collect() -> list[dict]:
            collected = []
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: ") :])
                collected.append(event)
                if event["type"] == "decision":
                    break
            return collected

        # Subscription is already live by the time this `async with` returned (the
        # route handler subscribes before StreamingResponse begins sending -- see
        # routers/events.py), so starting `adjudicate` now cannot race a message it
        # would otherwise miss.
        collector = asyncio.create_task(collect())

        await adjudicate(
            case.id,
            db_pool,
            StubPolicyClient(hits=HITS),
            StubMemberClient(sleep_studies=[_study(ahi=20.0)]),
            StubLLM(RAW_RESPONSE),
            GateThresholds(),
        )

        streamed = await asyncio.wait_for(collector, timeout=10)

    stored_response = await client.get(f"/cases/{case.id}/events")
    stored = stored_response.json()

    assert len(streamed) == len(stored)
    assert [e["seq"] for e in streamed] == [e["seq"] for e in stored]
    assert [e["type"] for e in streamed] == [e["type"] for e in stored]
    assert streamed[-1]["payload"] == stored[-1]["payload"]
