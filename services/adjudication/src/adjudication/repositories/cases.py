"""Raw SQL for the `cases` table.

`conn` below accepts anything with asyncpg's `fetchrow` coroutine -- a pooled
`Connection` (as `db_session` gives tests) or the `Pool` itself (as the pipeline's
single reads and the early `queued` -> `running` transition use, since neither needs
to share a transaction with anything else). See `repositories/case_events.py` for the
one place in this service where *not* sharing a transaction is load-bearing."""

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
