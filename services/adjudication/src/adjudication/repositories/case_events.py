"""Raw SQL for the append-only `case_events` table.

`append` is the single write path onto this table -- Task 8's Redis publish calls this
same function, so the stored log and the live SSE stream can never diverge (task-7
brief, decision 8; task-8 brief, decision 4). See `bind` below for how that publish is
wired in without changing `append`'s call signature, which every existing caller in
`services/pipeline.py` relies on staying `(pool, case_id, type, payload)`.

Takes the **pool**, never an acquired connection, and that is load-bearing rather than
stylistic. `Pool.fetchrow()` acquires a fresh connection for this one statement and
releases it immediately, independent of whatever transaction the caller may be running
elsewhere -- so an event commits the moment its stage finishes, and survives even if a
later stage fails and rolls back its own transaction (task-7 brief, decision 7: "a
stage that ran must stay in the audit trail even if the case later fails"). Passing an
already-acquired `Connection` instead would work mechanically -- it exposes the same
`fetchrow` -- but would silently join this insert to that connection's open
transaction, which is exactly the coupling this function exists to avoid."""

import json

import asyncpg

from adjudication.models.case_event import CaseEvent

_COLUMNS = "id, case_id, seq, type, payload, created_at"

#: Set by `bind`, read by `append`. Module-level rather than threaded through every
#: call site: `services/pipeline.py`'s five `append` calls are task-7 code this task
#: does not touch, and the brief's own interface note says as much ("the pipeline is
#: already written to funnel every event through that function"). `None` until a
#: process calls `bind` -- every existing test, none of which does, keeps publishing
#: nothing and behaves exactly as it did before this task.
_redis = None


def channel(case_id: str) -> str:
    """The Redis Pub/Sub channel `case_id`'s events publish to and
    `routers/events.py`'s `/stream` subscribes to -- named in this one place so a
    typo cannot make the two sides disagree."""
    return f"case_events:{case_id}"


def bind(redis_client) -> None:
    """Wire `append` to also publish each event onto Redis Pub/Sub. Called exactly
    once, from `worker.py`'s `main()`: the worker is the only process that ever calls
    `adjudicate`, hence the only process whose `append` calls need to reach a live
    subscriber. The HTTP process never calls this -- it only ever reads (`GET
    .../events`) or subscribes (`GET .../stream`)."""
    global _redis
    _redis = redis_client


def to_wire(event: CaseEvent) -> dict:
    """The one JSON shape both `GET /cases/{id}/events` (the stored log) and the
    Pub/Sub publish below use. A second, independently-written shape for either side
    is exactly the drift that would make "the SSE stream and the stored log render the
    same sequence" false in substance even if `seq` still matched."""
    return {
        "id": event.id,
        "case_id": event.case_id,
        "seq": event.seq,
        "type": event.type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
    }


def _row_to_event(row: asyncpg.Record) -> CaseEvent:
    return CaseEvent(
        id=row["id"],
        case_id=str(row["case_id"]),
        seq=row["seq"],
        type=row["type"],
        payload=json.loads(row["payload"]),
        created_at=row["created_at"],
    )


async def append(pool, case_id: str, event_type: str, payload: dict) -> CaseEvent:
    """Append one event to `case_id`'s log, and publish it to `channel(case_id)` if
    `bind` has wired a Redis client in.

    `seq` is allocated inside the INSERT itself (`SELECT COALESCE(MAX(seq), 0) + 1 ...`)
    rather than read in Python and written back in a second statement, so two
    concurrent appends for the same case race on `UNIQUE (case_id, seq)` -- a loud
    constraint violation -- instead of one silently overwriting the other's seq
    (task-7 brief, decision 7). The publish happens after that INSERT is durably
    committed (`pool.fetchrow` runs and releases its own connection -- see the module
    docstring), so a subscriber can never observe an event over Pub/Sub that a
    concurrent read of the stored log would not also find."""
    row = await pool.fetchrow(
        f"""
        INSERT INTO case_events (case_id, seq, type, payload)
        VALUES ($1, (SELECT COALESCE(MAX(seq), 0) + 1 FROM case_events WHERE case_id = $1),
                $2, $3::jsonb)
        RETURNING {_COLUMNS}
        """,
        case_id,
        event_type,
        json.dumps(payload),
    )
    event = _row_to_event(row)
    if _redis is not None:
        await _redis.publish(channel(case_id), json.dumps(to_wire(event)))
    return event


async def list_for_case(pool, case_id: str) -> list[CaseEvent]:
    """The stored log, in `seq` order -- what `GET /cases/{id}/events` replays, and
    what `tests/test_routes.py` compares against the live `/stream` to prove the two
    never diverge."""
    rows = await pool.fetch(
        f"SELECT {_COLUMNS} FROM case_events WHERE case_id = $1 ORDER BY seq", case_id
    )
    return [_row_to_event(row) for row in rows]
