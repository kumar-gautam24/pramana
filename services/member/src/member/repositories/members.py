"""Raw SQL for the `members` table.

Every statement here is one a reviewer can check against the policy it implements --
see docs/decisions/0013."""

from datetime import date

import asyncpg

from member.models import Member

_COLUMNS = "id, birth_date, sex, coverage_start, coverage_end"


def _row_to_member(row: asyncpg.Record) -> Member:
    return Member(
        id=row["id"],
        birth_date=row["birth_date"],
        sex=row["sex"],
        coverage_start=row["coverage_start"],
        coverage_end=row["coverage_end"],
    )


async def get(conn, member_id: str) -> Member | None:
    row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM members WHERE id = $1", member_id)
    return _row_to_member(row) if row is not None else None


async def existing_ids(conn, ids: list[str]) -> set[str]:
    """Which of `ids` already have a row here -- seed_population's idempotency check,
    run before any row for a new member is written."""
    rows = await conn.fetch("SELECT id FROM members WHERE id = ANY($1::text[])", ids)
    return {row["id"] for row in rows}


async def insert(
    conn,
    *,
    id: str,
    birth_date: date,
    sex: str,
    coverage_start: date,
    coverage_end: date | None,
) -> Member:
    row = await conn.fetchrow(
        f"""
        INSERT INTO members (id, birth_date, sex, coverage_start, coverage_end)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING {_COLUMNS}
        """,
        id,
        birth_date,
        sex,
        coverage_start,
        coverage_end,
    )
    return _row_to_member(row)


async def coverage_active(conn, member_id: str, on: date) -> bool:
    """Whether `member_id` had active coverage on `on`.

    Both bounds are inclusive: a member whose coverage ends on `on` was still covered
    that day, so `coverage_end < on` (not `<=`) is what excludes them. `coverage_end IS
    NULL` is open-ended and never excludes. Read back as a Member rather than pushed
    into a single boolean SQL expression so the 404-vs-false distinction main.py needs
    (member missing vs. member present but uncovered) stays available to the caller.
    """
    member = await get(conn, member_id)
    if member is None:
        return False
    if member.coverage_start > on:
        return False
    if member.coverage_end is not None and member.coverage_end < on:
        return False
    return True
