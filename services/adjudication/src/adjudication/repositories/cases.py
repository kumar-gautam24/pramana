"""Raw SQL for the `cases` table.

`conn` below accepts anything with asyncpg's `fetchrow` coroutine -- a pooled
`Connection` (as `db_session` gives tests) or the `Pool` itself (as the pipeline's
single reads and the early `queued` -> `running` transition use, since neither needs
to share a transaction with anything else). See `repositories/case_events.py` for the
one place in this service where *not* sharing a transaction is load-bearing."""

import json

import asyncpg

from adjudication.models.case import Case

_COLUMNS = (
    "id, member_id, requested_code, icd10, date_of_service, kind, status, created_at, "
    "request_text, idempotency_key"
)


def _row_to_case(row: asyncpg.Record) -> Case:
    return Case(
        # asyncpg hands back its own UUID type for a `uuid` column, not `str` --
        # `models.case.Case.id` is typed `str`, so this is the one place that
        # conversion happens rather than leaving every caller to remember it.
        id=str(row["id"]),
        member_id=row["member_id"],
        requested_code=row["requested_code"],
        icd10=row["icd10"],
        date_of_service=row["date_of_service"],
        kind=row["kind"],
        status=row["status"],
        created_at=row["created_at"],
        request_text=row["request_text"],
        idempotency_key=row["idempotency_key"],
    )


async def insert(
    conn,
    *,
    member_id: str,
    requested_code: str,
    icd10: str,
    date_of_service,
    kind: str,
    request_text: str | None = None,
    idempotency_key: str | None = None,
) -> Case:
    """`status` is left to its column default (`queued`) -- Task 8's `POST /cases`
    enqueues a case before anything has run, and the pipeline itself is what advances
    it via `update_status` below. `request_text` defaults to `None`: most callers of
    this function today (every test that doesn't care about retrieval quality) have no
    narrative to give it, and the column is nullable for exactly that reason
    (migrations/0002_cases_request_text.sql).

    Raises `asyncpg.UniqueViolationError` (constraint `uq_cases_idempotency_key`) if
    `idempotency_key` is not `None` and already belongs to another case -- this
    function does not catch it. `services.intake.submit_case` is where that violation
    becomes "return the existing case" instead of a 500 (task-8 brief, decision 1);
    this module stays a plain insert, consistent with every other repository here."""
    row = await conn.fetchrow(
        f"""
        INSERT INTO cases (
            member_id, requested_code, icd10, date_of_service, kind, request_text,
            idempotency_key
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_COLUMNS}
        """,
        member_id,
        requested_code,
        icd10,
        date_of_service,
        kind,
        request_text,
        idempotency_key,
    )
    return _row_to_case(row)


async def get(conn, case_id: str) -> Case | None:
    row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM cases WHERE id = $1", case_id)
    return _row_to_case(row) if row is not None else None


async def get_by_idempotency_key(conn, idempotency_key: str) -> Case | None:
    """The lookup `services.intake.submit_case` uses after `insert` above raises on a
    repeated key, to answer a retried `POST /cases` with the case that retry's first
    attempt actually created."""
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM cases WHERE idempotency_key = $1", idempotency_key
    )
    return _row_to_case(row) if row is not None else None


async def update_status(conn, case_id: str, status: str) -> Case | None:
    """`status` is one of the four the column's CHECK constraint allows -- the caller
    (the pipeline) is the one place that decides which, this module only writes it.

    Returns `None` for an unknown `case_id`, mirroring `get` above (finding 10, fix
    round 1): an UPDATE that matches no row hands `fetchrow` back `None`, and without
    this guard `_row_to_case` would fail on it with a bare `TypeError:
    'NoneType' object is not subscriptable` -- a caller's typo in a case id deserves a
    clear "no such case", not a stack trace pointing at a dict lookup."""
    row = await conn.fetchrow(
        f"UPDATE cases SET status = $2 WHERE id = $1 RETURNING {_COLUMNS}",
        case_id,
        status,
    )
    return _row_to_case(row) if row is not None else None


async def list_with_determinations(
    conn, *, outcome: str | None = None, status: str | None = None, limit: int = 100
) -> list[dict]:
    """Cases with their current determination attached, newest first -- what the review
    queue is made of.

    The determination is joined via a lateral picking the newest row per case, because a
    case may be adjudicated more than once and the current answer is the latest one (see
    `determinations`' own comment in migration 0001 about why superseded rows survive). A
    plain join would return one row per attempt and show a reviewer the same case twice,
    once with an answer that is no longer true.

    Ordered by `created_at DESC, id DESC`: the timestamp alone is not a total order, and
    two cases submitted in the same millisecond must still come back in a stable order or
    a paging reviewer sees one twice and another never.
    """
    conditions = []
    args: list[object] = []

    if status is not None:
        args.append(status)
        conditions.append(f"c.status = ${len(args)}")
    if outcome is not None:
        args.append(outcome)
        conditions.append(f"d.outcome = ${len(args)}")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    args.append(limit)

    rows = await conn.fetch(
        f"""
        SELECT {', '.join(f'c.{column.strip()}' for column in _COLUMNS.split(','))},
               d.outcome, d.reason, d.blocking, d.winning_set, d.created_at AS decided_at
        FROM cases c
        LEFT JOIN LATERAL (
            SELECT outcome, reason, blocking, winning_set, created_at
            FROM determinations
            WHERE case_id = c.id
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        ) d ON true
        {where}
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT ${len(args)}
        """,
        *args,
    )

    return [
        {
            "case": _row_to_case(row),
            "outcome": row["outcome"],
            "reason": row["reason"],
            "blocking": json.loads(row["blocking"]) if row["blocking"] is not None else None,
            "winning_set": row["winning_set"],
            "decided_at": row["decided_at"],
        }
        for row in rows
    ]
