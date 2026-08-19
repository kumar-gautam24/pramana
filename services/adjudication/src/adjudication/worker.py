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
`adjudicate` already converts every *expected* failure -- no eligibility record, no
governing policy, an upstream timeout -- into an ordinary escalation; anything that
still escapes it is a genuine bug, and `_process_one` below is the only layer left that
can catch it and record that the case needs a human to look at why, rather than sitting
in `running` forever with nothing in `case_events` explaining what happened."""

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
from adjudication.services.pipeline import adjudicate
from adjudication.services.policy_client import PolicyClient

logger = logging.getLogger(__name__)

#: See the module docstring: one fixed consumer, matching decision 5's "one case at a
#: time" -- there is never a second consumer for `run` to distinguish itself from.
CONSUMER = "worker"
#: How long a single XREADGROUP call blocks for a new message before this loop checks
#: its own `iterations` bound again. Short enough that a test bounding `iterations`
#: returns promptly when the stream stays empty; long enough not to busy-loop Redis in
#: production.
BLOCK_MS = 5_000


async def _process_one(
    pool: asyncpg.Pool,
    policy_client: PolicyClient,
    member_client: MemberClient,
    llm: LLMProvider,
    thresholds: GateThresholds,
    case_id: str,
) -> None:
    """Run one case through the pipeline; never raise back to the read loop -- one
    bad message or one buggy case must not take the whole worker process down, since
    every other case waiting behind it in the stream would then never run either."""
    try:
        await adjudicate(case_id, pool, policy_client, member_client, llm, thresholds)
    except LookupError:
        # The id came off the stream but names no case (a stale message, a bad test
        # fixture). Nothing to mark failed -- there is no row. Logged, not silent: a
        # message that resolves to nothing is still worth knowing about.
        logger.exception("case %s not found; dropping stream message", case_id)
    except Exception:
        # Decision 6: this is the one place that converts "the pipeline crashed" into
        # a fact the reviewer queue can see, rather than a case stuck in `running`.
        logger.exception("case %s crashed during adjudication; marking failed", case_id)
        await cases_repo.update_status(pool, case_id, "failed")


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

    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
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
