"""Raw SQL for the `cases` table.

`conn` below accepts anything with asyncpg's `fetchrow` coroutine -- a pooled
`Connection` (as `db_session` gives tests) or the `Pool` itself (as the pipeline's
single reads and the early `queued` -> `running` transition use, since neither needs
to share a transaction with anything else). See `repositories/case_events.py` for the
one place in this service where *not* sharing a transaction is load-bearing."""

import asyncpg

from adjudication.models.case import Case

_COLUMNS = "id, member_id, requested_code, icd10, date_of_service, kind, status, created_at"


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
    )


async def insert(
    conn,
    *,
    member_id: str,
    requested_code: str,
    icd10: str,
    date_of_service,
    kind: str,
) -> Case:
    """`status` is left to its column default (`queued`) -- Task 8's `POST /cases`
    enqueues a case before anything has run, and the pipeline itself is what advances
    it via `update_status` below."""
    row = await conn.fetchrow(
        f"""
        INSERT INTO cases (member_id, requested_code, icd10, date_of_service, kind)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING {_COLUMNS}
        """,
        member_id,
        requested_code,
        icd10,
        date_of_service,
        kind,
    )
    return _row_to_case(row)


async def get(conn, case_id: str) -> Case | None:
    row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM cases WHERE id = $1", case_id)
    return _row_to_case(row) if row is not None else None


async def update_status(conn, case_id: str, status: str) -> Case:
    """`status` is one of the four the column's CHECK constraint allows -- the caller
    (the pipeline) is the one place that decides which, this module only writes it."""
    row = await conn.fetchrow(
        f"UPDATE cases SET status = $2 WHERE id = $1 RETURNING {_COLUMNS}",
        case_id,
        status,
    )
    return _row_to_case(row)
