"""Tests for `worker.py` and `services/queue.py`.

Runs against a real Redis (`redis_client`, see conftest.py) and `db_pool` -- the same
posture `test_pipeline.py` takes towards Postgres, and for the identical reason: the
worker's own job is exactly this integration (Redis Streams, the pipeline, and
`cases.status`), so a mock of any one of those would be testing something other than
what the worker actually does.

Every test names its own stream/group (a fresh uuid4) rather than `services.queue`'s
production constants, so tests never see another test's or a previous run's pending
entries -- there is no per-test rollback for Redis the way `db_session` gives Postgres.

Stub `PolicyClient`, `MemberClient` and `LLMProvider` only -- no live upstream, no live
model, matching `test_pipeline.py`'s own doubles (reused here for the happy-path
fixture data)."""

from datetime import date
from uuid import uuid4

import pytest
from conftest import TEST_REDIS_URL
from fixtures.ncd_240_4_extraction import HITS, RAW_RESPONSE
from pramana_common.gate import GateThresholds

from adjudication.repositories import cases as cases_repo
from adjudication.services import queue
from adjudication.services.member_client import Adherence, CoverageStatus
from adjudication.services.upstream import UpstreamUnavailable
from adjudication.worker import (
    BLOCK_MS,
    CONSUMER,
    RETRY_BUDGET_S,
    RETRY_DELAYS_S,
    SOCKET_TIMEOUT_S,
    _retry_delay,
    run,
)

MEMBER_ID = "m-1"
DATE_OF_SERVICE = date(2026, 1, 15)

#: A test never waits the production 5s BLOCK_MS for a message that will never arrive.
TEST_BLOCK_MS = 100


# --- test doubles (mirroring test_pipeline.py's) ------------------------------------


class StubPolicyClient:
    def __init__(self, hits=None):
        self._hits = [] if hits is None else hits

    async def search(self, query, date_of_service, limit):
        if isinstance(self._hits, Exception):
            raise self._hits
        return self._hits


class StubMemberClient:
    def __init__(self, *, coverage=None, sleep_studies=None):
        self._coverage = CoverageStatus.ACTIVE if coverage is None else coverage
        self._sleep_studies = [] if sleep_studies is None else sleep_studies

    async def coverage(self, member_id, on):
        if isinstance(self._coverage, Exception):
            raise self._coverage
        return self._coverage

    async def sleep_studies(self, member_id, before):
        if isinstance(self._sleep_studies, Exception):
            raise self._sleep_studies
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
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class CrashingMemberClient(StubMemberClient):
    """`coverage` raises a plain bug, not `UpstreamUnavailable` -- the pipeline
    re-raises this rather than turning it into an escalation (task-7 brief, decision
    6), which is exactly the case decision 6 makes the worker responsible for."""

    async def coverage(self, member_id, on):
        raise RuntimeError("boom: not an UpstreamUnavailable")


async def _insert_case(pool, **overrides):
    values = dict(
        member_id=MEMBER_ID,
        requested_code="95810",
        icd10="G47.33",
        date_of_service=DATE_OF_SERVICE,
        kind="initial",
    )
    values.update(overrides)
    return await cases_repo.insert(pool, **values)


def _names() -> tuple[str, str]:
    """A fresh stream and group per test -- see the module docstring."""
    unique = uuid4().hex
    return f"test:cases:{unique}", f"test:group:{unique}"


async def _run1(redis_client, pool, policy_client, member_client, llm, *, stream, group):
    """`run` bounded to one read attempt, with a short block timeout -- every test
    below wants exactly one message processed (or one empty-stream check), never the
    production's block-forever loop."""
    await run(
        redis_client,
        pool,
        policy_client,
        member_client,
        llm,
        GateThresholds(),
        stream=stream,
        group=group,
        consumer=CONSUMER,
        iterations=1,
        block_ms=TEST_BLOCK_MS,
    )


# === queue.py ========================================================================


async def test_ensure_group_is_idempotent(redis_client):
    stream, group = _names()

    await queue.ensure_group(redis_client, stream=stream, group=group)
    await queue.ensure_group(redis_client, stream=stream, group=group)  # must not raise

    info = await redis_client.xinfo_groups(stream)
    assert len(info) == 1


async def test_enqueue_adds_one_entry_to_the_stream(redis_client):
    stream, _group = _names()

    await queue.enqueue(redis_client, "case-123", stream=stream)

    assert await redis_client.xlen(stream) == 1


# === worker.run: happy path ==========================================================


async def test_run_processes_a_case_and_acknowledges_the_message(db_pool, redis_client):
    stream, group = _names()
    case = await _insert_case(db_pool)
    await queue.enqueue(redis_client, case.id, stream=stream)

    await _run1(
        redis_client,
        db_pool,
        StubPolicyClient(hits=HITS),
        StubMemberClient(sleep_studies=[]),
        StubLLM(RAW_RESPONSE),
        stream=stream,
        group=group,
    )

    stored = await cases_repo.get(db_pool, case.id)
    assert stored.status == "decided"

    pending = await redis_client.xpending(stream, group)
    assert pending["pending"] == 0


# === worker.run: decision 6, a crash ends the case `failed` =========================


async def test_run_marks_the_case_failed_when_adjudicate_raises_unexpectedly(
    db_pool, redis_client
):
    stream, group = _names()
    case = await _insert_case(db_pool)
    await queue.enqueue(redis_client, case.id, stream=stream)

    # The worker must not itself raise: one crashing case must not take the read loop
    # down with it, or every case behind it in the stream would never run either.
    await _run1(
        redis_client,
        db_pool,
        StubPolicyClient(hits=HITS),
        CrashingMemberClient(),
        StubLLM(RAW_RESPONSE),
        stream=stream,
        group=group,
    )

    stored = await cases_repo.get(db_pool, case.id)
    assert stored.status == "failed"

    # Still acknowledged: `failed` is a terminal, visible state, and retrying the same
    # bug forever against an unacked message would never converge (decision 6).
    pending = await redis_client.xpending(stream, group)
    assert pending["pending"] == 0


# === worker.run: crash recovery via the consumer group ==============================


async def test_run_reprocesses_a_message_left_unacked_by_a_crashed_consumer(
    db_pool, redis_client
):
    """Simulates a worker process that read a message and then crashed before
    acknowledging it -- the brief's own requirement: "an unacknowledged case returns
    after a crash." A fresh `run()` call, using the same stable CONSUMER name, must
    pick that message back up from its own pending list rather than waiting forever for
    a new one."""
    stream, group = _names()
    case = await _insert_case(db_pool)
    await queue.ensure_group(redis_client, stream=stream, group=group)
    await queue.enqueue(redis_client, case.id, stream=stream)

    # Read it as CONSUMER would, but never ack -- this is the simulated crash.
    crashed_read = await redis_client.xreadgroup(group, CONSUMER, {stream: ">"}, count=1)
    assert crashed_read and crashed_read[0][1], "setup: the crash must have read a message"

    await _run1(
        redis_client,
        db_pool,
        StubPolicyClient(hits=HITS),
        StubMemberClient(sleep_studies=[]),
        StubLLM(RAW_RESPONSE),
        stream=stream,
        group=group,
    )

    stored = await cases_repo.get(db_pool, case.id)
    assert stored.status == "decided"

    pending = await redis_client.xpending(stream, group)
    assert pending["pending"] == 0


async def test_run_drops_a_message_naming_no_such_case(db_pool, redis_client):
    """`adjudicate` raises `LookupError` for an id that resolves to nothing (a stale
    message, a bad producer) -- the worker must log and move on, not crash, and there
    is no case row to mark `failed`."""
    stream, group = _names()
    await queue.enqueue(redis_client, str(uuid4()), stream=stream)

    await _run1(
        redis_client,
        db_pool,
        StubPolicyClient(hits=HITS),
        StubMemberClient(),
        StubLLM(RAW_RESPONSE),
        stream=stream,
        group=group,
    )

    pending = await redis_client.xpending(stream, group)
    assert pending["pending"] == 0


async def test_run_with_an_empty_stream_returns_after_its_bounded_iterations(
    db_pool, redis_client
):
    """No message at all: `run` must still return (not block forever) once
    `iterations` is exhausted -- what makes this function testable at all."""
    stream, group = _names()

    await _run1(
        redis_client,
        db_pool,
        StubPolicyClient(),
        StubMemberClient(),
        StubLLM(RAW_RESPONSE),
        stream=stream,
        group=group,
    )
    # No assertion beyond "this returned promptly" -- the point is the call above does
    # not hang on the production BLOCK_MS.


@pytest.mark.parametrize("kind", ["initial", "continuation"])
async def test_run_processes_either_case_kind(db_pool, redis_client, kind):
    """Not a worker-specific concern on its own, but a cheap way to confirm nothing in
    the read/process/ack loop assumes `kind == "initial"`."""
    stream, group = _names()
    case = await _insert_case(db_pool, kind=kind)
    await queue.enqueue(redis_client, case.id, stream=stream)

    await _run1(
        redis_client,
        db_pool,
        StubPolicyClient(hits=HITS),
        StubMemberClient(sleep_studies=[]),
        StubLLM(RAW_RESPONSE),
        stream=stream,
        group=group,
    )

    stored = await cases_repo.get(db_pool, case.id)
    assert stored.status == "decided"


# --- the blocking-read deadline -----------------------------------------------------
#
# These exist because the worker died five seconds after start against a live Redis
# while all 287 tests were green. redis-py 8's DEFAULT_SOCKET_TIMEOUT is 5s, exactly
# BLOCK_MS, so a blocking read over an idle stream raced its own socket deadline and
# raised instead of returning empty. Every test above passes TEST_BLOCK_MS (100ms), which
# is why none of them could see it: the bug lives only at the production constant.


def test_the_socket_deadline_stays_above_the_block_window():
    """The invariant, checked directly so it cannot drift. Raising BLOCK_MS to meet or
    exceed the socket deadline reintroduces the exact failure."""
    assert SOCKET_TIMEOUT_S > BLOCK_MS / 1_000


async def test_an_idle_stream_at_the_production_block_value_returns_rather_than_raising(
    db_pool, redis_client
):
    """The regression test proper: the production BLOCK_MS, an empty stream, and a client
    carrying the production socket deadline. Costs one BLOCK_MS of wall clock, which is
    the price of exercising the constant that actually ships rather than a stand-in.

    `redis_client` here must be built the way `worker.main()` builds it -- a fixture
    client on redis-py's default deadline would reproduce the bug rather than the fix."""
    from redis.asyncio import Redis

    stream, group = _names()

    # Built exactly the way worker.main() builds it -- from_url with the shipped
    # deadline. The `redis_client` fixture is deliberately not reused here: it carries
    # redis-py's default deadline, which is the bug rather than the fix.
    production_client = Redis.from_url(
        TEST_REDIS_URL, decode_responses=True, socket_timeout=SOCKET_TIMEOUT_S
    )

    try:
        await run(
            production_client,
            db_pool,
            StubPolicyClient(),
            StubMemberClient(),
            StubLLM(RAW_RESPONSE),
            GateThresholds(),
            stream=stream,
            group=group,
            iterations=1,
            block_ms=BLOCK_MS,
        )
    finally:
        await production_client.aclose()


# --- the retry ladder's bound on its own total ----------------------------------------
#
# `RETRY_BUDGET_S` was added in 6ebac3e and its bound was checked by hand once, then held
# by a comment. That is the weakest form this project accepts for a cross-service
# invariant, and the invariant is a real one: the ladder promises `evals` that a retried
# case still settles inside `case_timeout_seconds`, so a provider sending a long
# `Retry-After` on every rung must not be able to walk the case past that window.


def _ladder_total(retry_after_values):
    """Drive `_retry_delay` the way `_process_one` does -- rung by rung, accumulating
    `waited` -- and return the delays it chose plus their total.

    Reimplements only the accumulation, not the policy: every delay comes from the real
    `_retry_delay`, and the loop's shape (stop when the budget is spent) is the one
    `_process_one` uses. A test that recomputed the delays would prove nothing."""
    delays = []
    waited = 0.0
    for rung in range(len(RETRY_DELAYS_S)):
        if waited >= RETRY_BUDGET_S:
            break
        exc = UpstreamUnavailable(
            "llm", "status 429", transient=True, retry_after=retry_after_values[rung]
        )
        delay = _retry_delay(rung, exc, waited)
        delays.append(delay)
        waited += delay
    return delays, waited


@pytest.mark.parametrize(
    "retry_after",
    [
        pytest.param([None, None, None], id="no-advice"),
        pytest.param([0.0, 0.0, 0.0], id="zero"),
        pytest.param([5.0, 5.0, 5.0], id="shorter-than-the-rungs"),
        pytest.param([30.0, 30.0, 30.0], id="around-the-rungs"),
        # The measured trigger for 6ebac3e: 90 on each of three rungs waited 270s against a
        # 240s case timeout, so the retries that exist to stop the harness recording the
        # case unfinished were what guaranteed it.
        pytest.param([90.0, 90.0, 90.0], id="past-the-budget-on-every-rung"),
        pytest.param([3600.0, 3600.0, 3600.0], id="absurd"),
        # Observed live on 2026-08-22 against Groq's 8000 TPM ceiling.
        pytest.param([6.24, 18.31, 34.85], id="measured-groq-429s"),
        pytest.param([None, 3600.0, None], id="one-absurd-rung-among-normal-ones"),
    ],
)
def test_the_ladder_never_waits_longer_than_its_budget(retry_after):
    """The bound `6ebac3e` documented, over every shape of provider advice."""
    delays, total = _ladder_total(retry_after)

    assert total <= RETRY_BUDGET_S, f"waited {total}s against a budget of {RETRY_BUDGET_S}s"
    assert all(d >= 0 for d in delays), f"a negative delay would busy-wait: {delays}"


def test_the_server_wins_when_it_asks_for_longer_than_the_rung():
    """A rate limiter knows how much of its window is left and this process does not, so its
    advice beats the ladder's own spacing -- inside the budget."""
    exc = UpstreamUnavailable("llm", "status 429", transient=True, retry_after=30.0)

    assert _retry_delay(0, exc, 0.0) == 30.0


def test_the_ladder_wins_when_the_server_asks_for_less():
    """The floor matters: a provider that says "0" while still refusing is a busy-wait, and
    the ladder's spacing is what stops three more 429s in a row."""
    exc = UpstreamUnavailable("llm", "status 429", transient=True, retry_after=0.0)

    assert _retry_delay(0, exc, 0.0) == RETRY_DELAYS_S[0]


def test_no_advice_falls_back_to_the_rung():
    exc = UpstreamUnavailable("llm", "status 503", transient=True)

    assert _retry_delay(1, exc, 0.0) == RETRY_DELAYS_S[1]


def test_a_rung_is_clipped_to_what_is_left_of_the_budget():
    """Not skipped and not fired at zero: clipped. `_process_one` ends the ladder when the
    budget is spent, so the last rung's job is to use exactly the remainder."""
    spent = RETRY_BUDGET_S - 4.0
    exc = UpstreamUnavailable("llm", "status 429", transient=True, retry_after=3600.0)

    assert _retry_delay(2, exc, spent) == pytest.approx(4.0)


def test_the_budget_is_the_ladder_s_own_nominal_total():
    """Pinned rather than assumed. If a rung is added or retuned and the budget is not
    derived from the rungs, the promise made to `evals` silently changes -- which is the
    whole reason `RETRY_BUDGET_S` is a sum and not a constant someone chose."""
    assert RETRY_BUDGET_S == sum(RETRY_DELAYS_S)


def test_the_ladder_settles_well_inside_the_harness_case_timeout():
    """The cross-service bound, stated as a test rather than as prose in two files. `evals`'
    default `case_timeout_seconds` is 240s; the ladder may spend at most `RETRY_BUDGET_S` of
    that waiting, and the pipeline needs the rest. If either number moves without the other,
    this fails -- which is what "they have to move together" should mean operationally."""
    evals_case_timeout_seconds = 240.0

    assert RETRY_BUDGET_S < evals_case_timeout_seconds / 2, (
        f"a retried case may wait {RETRY_BUDGET_S}s of a {evals_case_timeout_seconds}s "
        "budget, leaving too little for the pipeline itself"
    )
