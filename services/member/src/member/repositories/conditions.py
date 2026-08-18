"""Raw SQL for the `conditions` table."""

from datetime import date

import asyncpg

from member.models import Condition

_COLUMNS = "id, member_id, code, description, onset_date"


def _row_to_condition(row: asyncpg.Record) -> Condition:
    return Condition(
        id=row["id"],
        member_id=row["member_id"],
        code=row["code"],
        description=row["description"],
        onset_date=row["onset_date"],
    )


async def insert(
    conn, *, member_id: str, code: str, description: str, onset_date: date
) -> Condition:
    row = await conn.fetchrow(
        f"""
        INSERT INTO conditions (member_id, code, description, onset_date)
        VALUES ($1, $2, $3, $4)
        RETURNING {_COLUMNS}
        """,
        member_id,
        code,
        description,
        onset_date,
    )
    return _row_to_condition(row)


async def conditions_before(
    conn, member_id: str, on: date, codes: list[str]
) -> list[Condition]:
    """Conditions among `codes` with onset on or before `on`. Same "before the date of
    service" boundary as sleep studies, restricted to the codes a criterion names so an
    unrelated comorbidity in the record can't be mistaken for a relevant one."""
    rows = await conn.fetch(
        f"""
        SELECT {_COLUMNS} FROM conditions
        WHERE member_id = $1 AND onset_date <= $2 AND code = ANY($3::text[])
        ORDER BY onset_date
        """,
        member_id,
        on,
        codes,
    )
    return [_row_to_condition(row) for row in rows]
