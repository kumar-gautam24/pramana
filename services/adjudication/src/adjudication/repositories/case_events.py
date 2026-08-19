"""Raw SQL for the append-only `case_events` table.

`append` is the single write path onto this table -- Task 8's Redis publish is meant to
call this same function, so the stored log and the live SSE stream can never diverge
(task-7 brief, decision 8).

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
    """Append one event to `case_id`'s log.

    `seq` is allocated inside the INSERT itself (`SELECT COALESCE(MAX(seq), 0) + 1 ...`)
    rather than read in Python and written back in a second statement, so two
    concurrent appends for the same case race on `UNIQUE (case_id, seq)` -- a loud
    constraint violation -- instead of one silently overwriting the other's seq
    (task-7 brief, decision 7)."""
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
    return _row_to_event(row)
