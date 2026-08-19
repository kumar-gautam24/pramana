"""Redis Stream mechanics for the case-adjudication queue.

Shared by the producer (`services/intake.py`, on `POST /cases`) and the consumer
(`worker.py`), so both sides name the same stream and consumer group in exactly one
place -- a typo in either module cannot silently split them onto two different queues
that never talk to each other."""

from redis.asyncio import Redis
from redis.exceptions import ResponseError

#: The one stream `POST /cases` enqueues onto and `worker.py` consumes from in
#: production. Tests pass their own `stream`/`group` so a run doesn't collide with
#: state a previous test run left behind in the shared local Redis (there is no
#: per-test rollback for Redis the way `db_session` gives Postgres).
STREAM = "adjudication:cases"
GROUP = "workers"


async def ensure_group(redis_client: Redis, *, stream: str = STREAM, group: str = GROUP) -> None:
    """Idempotent: `BUSYGROUP` means `group` already exists, from an earlier boot of
    this same worker or an earlier test, and is not an error -- every other
    `ResponseError` is a real misconfiguration and propagates."""
    try:
        await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def enqueue(redis_client: Redis, case_id: str, *, stream: str = STREAM) -> None:
    """The single write path onto the stream -- `services/intake.py`'s only call after
    a case is newly inserted, never after an idempotent replay (task-8 brief, decision
    1: a retried submission must not adjudicate twice)."""
    await redis_client.xadd(stream, {"case_id": case_id})
