"""The standalone process that turns `POST /cases`' enqueued ids into adjudicated
cases. Runs as its own container (see docker-compose.yml's `adjudication-worker`
service) so the HTTP process can restart or scale independently of it -- there is no
worker code inside `main.py`'s lifespan.

**One case at a time, always** (task-8 brief, decision 5). The configured model
provider is on a free tier limited to 8000 tokens/minute and one extraction alone costs
about 2800, so roughly two cases a minute is the real ceiling regardless of how this
loop is written; running cases concurrently would only buy a faster stream of 429s that
escalate cases for a reason that has nothing to do with the member's own record.

**Crash recovery via the consumer group** (the brief's own requirement: "an
unacknowledged case returns after a crash"). `run` below reads this consumer's own
pending entries (Redis's `id="0"`) before asking for new ones (`id=">"`): on a fresh
boot the pending list is empty and that read is a no-op, but after a process crash
mid-case it is exactly the unacknowledged message the restart must retry. `CONSUMER` is
a single fixed name, not one derived from a hostname or pid -- decision 5 already rules
out more than one logical consumer running at a time, and a fixed name is what lets a
restarted process resume its predecessor's own pending list instead of starting a fresh,
empty one and leaving the crashed attempt's message stuck forever.

**A case whose `adjudicate` raises ends `failed`, not `running`** (decision 6).
`adjudicate` already converts every *expected permanent* failure -- no eligibility
record, no governing policy, a schema mismatch -- into an ordinary escalation; anything
that still escapes it is either a transient upstream failure (below) or a genuine bug,
and `_process_one` below is the only layer left that can catch the latter and record
that the case needs a human to look at why, rather than sitting in `running` forever
with nothing in `case_events` explaining what happened.

**Transient upstream failures are retried here, and nowhere else** (ADR-0020). A 429
from the model provider is a fact about our own rate limit; adjudicating on it would
put a case on a clinician's queue for a reason no clinician can act on, which is what
happened on the first live end-to-end run -- four of five cases escalated with
`upstream_unavailable` and none of the four had anything to do with the member's
record. The retry does not belong in `policy_client` or `llm`: their docstrings record
why, and the reason is exactly that this layer can write each attempt into
`case_events` where a reviewer and an auditor can see it, and a client cannot."""

import asyncio
import logging

import asyncpg
import httpx
from pramana_common.gate import GateThresholds
from redis.asyncio import Redis

from adjudication import db, startup
from adjudication.config import get_settings
from adjudication.repositories import case_events as case_events_repo
from adjudication.repositories import cases as cases_repo
from adjudication.services import queue
from adjudication.services.llm import LLMProvider, build_provider
from adjudication.services.member_client import MemberClient
from adjudication.services.pipeline import adjudicate, record_upstream_exhausted
from adjudication.services.policy_client import PolicyClient
from adjudication.services.upstream import UpstreamUnavailable

logger = logging.getLogger(__name__)

#: See the module docstring: one fixed consumer, matching decision 5's "one case at a
#: time" -- there is never a second consumer for `run` to distinguish itself from.
CONSUMER = "worker"
#: How long a single XREADGROUP call blocks for a new message before this loop checks
#: its own `iterations` bound again. Short enough that a test bounding `iterations`
#: returns promptly when the stream stays empty; long enough not to busy-loop Redis in
#: production.
BLOCK_MS = 5_000

#: The socket read deadline for the worker's Redis client, which MUST stay above the
#: block window above.
#:
#: redis-py 8 ships `DEFAULT_SOCKET_TIMEOUT = 5` seconds where earlier versions had no
#: deadline at all, and that default is exactly `BLOCK_MS`. A blocking XREADGROUP over
#: an idle stream therefore waits the full five seconds and loses a race against its own
#: socket deadline, raising `TimeoutError` instead of returning empty -- so the worker
#: died five seconds after start on any quiet queue, which is every deployment between
#: cases. Found by running the worker against a live Redis; the suite could not see it,
#: because every test passed a short block value and only production used the constant.
#:
#: The margin is what makes the deadline mean "Redis has stopped answering" rather than
#: "nothing arrived". Catching the timeout instead would have worked mechanically and
#: made a genuine Redis outage indistinguishable from an idle queue.
SOCKET_TIMEOUT_S = BLOCK_MS / 1_000 + 5.0

#: How long to wait before each retry of a case whose upstream failed transiently. The
#: length of this tuple is how many retries a case gets; one more attempt than that is the
#: total (ADR-0020).
#:
#: The rungs are sized against the failure that was actually measured, not chosen for
#: roundness. The trigger was a 429 from a provider metering 8000 tokens per minute while one
#: extraction costs about 2800 -- a bucket that refills over a minute. So the ladder has to
#: contain at least one wait longer than that window, or every attempt lands inside the same
#: exhausted minute and the retry buys nothing. The two short rungs cover the other transient
#: shapes, which clear in seconds: a restarting container, a reset connection, a momentary
#: 503.
#:
#: The ceiling on the total is `evals`' `case_timeout_seconds` (240s by default). A retried
#: case must still settle inside that window, or the harness records it as unfinished and the
#: retries produce nothing measurable. 85 seconds of waiting leaves the pipeline itself the
#: rest -- and if either number moves, they have to move together.
RETRY_DELAYS_S: tuple[float, ...] = (5.0, 20.0, 60.0)

#: The total a case may spend waiting, across every rung of the ladder.
#:
#: It is the ladder's own nominal total rather than a new number, because that total is what
#: the paragraph above already promises `evals` -- and a per-rung cap alone does not keep the
#: promise. A provider sending `Retry-After: 90` on each of three rungs would wait 270
#: seconds, which is past `case_timeout_seconds` (240s): the harness would record the case as
#: unfinished, so the retries that exist to stop exactly that would produce nothing
#: measurable, and the ladder would have broken the bound it documents. Honouring the
#: server's advice is worth doing only inside the budget the ladder already claims.
RETRY_BUDGET_S = sum(RETRY_DELAYS_S)


def _retry_delay(rung: int, exc: UpstreamUnavailable, waited: float) -> float:
    """How long to wait before attempt `rung + 2`, given what the upstream said and how much
    of `RETRY_BUDGET_S` the earlier rungs have already spent.

    The server's own `Retry-After` wins when it asks for longer than the ladder's rung: a
    rate limiter knows how much of its window is left and this process does not. It cannot
    push the case past the budget, and it cannot shorten a rung either -- a provider that says
    "0" while still refusing is a busy-wait, and the ladder's own spacing is what keeps the
    retries from being three more 429s in a row.

    Only called while budget remains -- `_process_one` ends the ladder instead when it does
    not, rather than firing the remaining rungs back to back at zero delay. A provider that
    asked for longer than the whole budget has said it will not serve this case in time, and
    two immediate re-asks spend a clinician's wait and the case's tokens to be told so twice."""
    asked = max(RETRY_DELAYS_S[rung], exc.retry_after or 0.0)
    return min(asked, RETRY_BUDGET_S - waited)


async def _process_one(
    pool: asyncpg.Pool,
    policy_client: PolicyClient,
    member_client: MemberClient,
    llm: LLMProvider,
    thresholds: GateThresholds,
    case_id: str,
) -> None:
    """Run one case through the pipeline, retrying a transient upstream failure; never
    raise back to the read loop -- one bad message or one buggy case must not take the
    whole worker process down, since every other case waiting behind it in the stream
    would then never run either.

    Each retry re-runs `adjudicate` from the top, so a retried case pays for its
    extraction again. That is the cost of not carrying a resumable half-finished case
    through the retry, and it is worth paying: `criteria.insert_many` already
    delete-then-inserts precisely so a second `adjudicate(case_id)` is well-defined (the
    at-least-once stream guarantees one anyway), and a partial-resume path would be a
    second, less-tested way for a case to reach the gate."""
    attempts = len(RETRY_DELAYS_S) + 1
    #: Spent so far on this case, so `RETRY_BUDGET_S` bounds the ladder's total and not
    #: merely each rung of it.
    waited = 0.0

    for attempt in range(1, attempts + 1):
        try:
            await adjudicate(case_id, pool, policy_client, member_client, llm, thresholds)
            return
        except LookupError:
            # The id came off the stream but names no case (a stale message, a bad test
            # fixture). Nothing to mark failed -- there is no row. Logged, not silent: a
            # message that resolves to nothing is still worth knowing about.
            logger.exception("case %s not found; dropping stream message", case_id)
            return
        except UpstreamUnavailable as exc:
            # The pipeline records every *permanent* upstream failure as a determination
            # itself, so one arriving here should always be transient. If it is not, the
            # pipeline has a raise site nothing guards -- logged loudly, but still recorded
            # as a determination rather than as `failed`, because "we could not obtain the
            # evidence" is true either way and a case with no determination at all tells a
            # reviewer strictly less.
            # Out of rungs, or out of budget: a provider whose `Retry-After` has already
            # consumed `RETRY_BUDGET_S` gets no further attempt, because the rungs left would
            # have to fire immediately and it has just told us it will refuse them.
            exhausted = attempt == attempts or waited >= RETRY_BUDGET_S
            if exhausted or not exc.transient:
                logger.exception(
                    "case %s: upstream %s unavailable after %s attempt(s) (transient=%s); "
                    "escalating",
                    case_id,
                    exc.service,
                    attempt,
                    exc.transient,
                )
                await record_upstream_exhausted(
                    pool,
                    case_id,
                    thresholds,
                    service=exc.service,
                    detail=exc.detail,
                    attempts=attempt,
                )
                return

            delay = _retry_delay(attempt - 1, exc, waited)
            waited += delay
            # Written to the audit trail before the wait, not after it: a case sitting
            # `running` for a minute must be explicable while it is happening, and this row
            # is the only thing that distinguishes "waiting out a rate limit" from "the
            # worker has hung". It is also the record that makes the retry honest -- the
            # audit claim is that everything the system did to reach a determination is in
            # this log, and a silent retry would falsify it.
            await case_events_repo.append(
                pool,
                case_id,
                "retry",
                {
                    "attempt": attempt,
                    "of": attempts,
                    "service": exc.service,
                    "detail": exc.detail,
                    "retrying_in_seconds": delay,
                },
            )
            logger.warning(
                "case %s: %s unavailable (%s); attempt %s of %s, retrying in %ss",
                case_id,
                exc.service,
                exc.detail,
                attempt,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
        except Exception:
            # Decision 6: this is the one place that converts "the pipeline crashed" into
            # a fact the reviewer queue can see, rather than a case stuck in `running`.
            logger.exception("case %s crashed during adjudication; marking failed", case_id)
            await cases_repo.update_status(pool, case_id, "failed")
            return


async def _read_one(
    redis_client: Redis, *, stream: str, group: str, consumer: str, block_ms: int
) -> tuple[str, dict] | None:
    """This consumer's own pending entries first, then a new one -- see the module
    docstring's crash-recovery paragraph for why the order matters."""
    pending = await redis_client.xreadgroup(group, consumer, {stream: "0"}, count=1)
    entries = pending[0][1] if pending else []
    if entries:
        return entries[0]

    fresh = await redis_client.xreadgroup(
        group, consumer, {stream: ">"}, count=1, block=block_ms
    )
    entries = fresh[0][1] if fresh else []
    return entries[0] if entries else None


async def run(
    redis_client: Redis,
    pool: asyncpg.Pool,
    policy_client: PolicyClient,
    member_client: MemberClient,
    llm: LLMProvider,
    thresholds: GateThresholds,
    *,
    stream: str = queue.STREAM,
    group: str = queue.GROUP,
    consumer: str = CONSUMER,
    iterations: int | None = None,
    block_ms: int = BLOCK_MS,
) -> None:
    """Consume `stream` one message at a time. `iterations=None` (the deployed
    worker's `main()` below) runs forever; a test passes a small integer so the call
    returns once that many read attempts have happened, and a small `block_ms` so a
    test exercising an empty stream returns quickly instead of blocking for the
    production default."""
    await queue.ensure_group(redis_client, stream=stream, group=group)

    count = 0
    while iterations is None or count < iterations:
        entry = await _read_one(
            redis_client, stream=stream, group=group, consumer=consumer, block_ms=block_ms
        )
        count += 1
        if entry is None:
            continue

        message_id, fields = entry
        await _process_one(pool, policy_client, member_client, llm, thresholds, fields["case_id"])
        # Acknowledged unconditionally, including after a crash `_process_one` caught:
        # `failed` is a terminal, visible state, and retrying the same bug forever
        # against an unacknowledged message would never converge -- see decision 6.
        await redis_client.xack(stream, group, message_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    await db.probe_fresh()
    pool = await db.pool()

    # Unlike main.py's client, this one performs blocking reads -- see
    # SOCKET_TIMEOUT_S for why the default deadline is not survivable here.
    redis_client = Redis.from_url(
        settings.redis_url, decode_responses=True, socket_timeout=SOCKET_TIMEOUT_S
    )
    await startup.probe_redis(redis_client)

    # The only process that calls `adjudicate`, hence the only process whose
    # `case_events.append` calls need to reach a live Pub/Sub subscriber -- see
    # `repositories/case_events.py`'s `bind` docstring and task-8 decision 4.
    case_events_repo.bind(redis_client)

    http_client = httpx.AsyncClient(timeout=30.0)
    try:
        # task-8 decision 3 has the HTTP process run this same guard in main.py's
        # lifespan, but this process -- not that one -- is the one that actually calls
        # the model (extraction, judgment verification): the API never does. Repeating
        # the check here means a worker started or restarted on its own (they scale
        # independently -- see the module docstring) still refuses to run cases against
        # a model that cannot honour a schema, rather than trusting a probe some other
        # process happened to pass at some earlier time.
        if settings.probe_llm_on_startup:
            await startup.probe_llm(settings, http_client)

        policy_client = PolicyClient(http_client, settings.policy_url)
        member_client = MemberClient(http_client, settings.member_url)
        llm = build_provider(settings, http_client)
        thresholds = GateThresholds(min_confidence=settings.min_confidence)

        logger.info("adjudication worker starting (provider=%s)", settings.llm_provider)
        await run(redis_client, pool, policy_client, member_client, llm, thresholds)
    finally:
        await http_client.aclose()
        await redis_client.aclose()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
