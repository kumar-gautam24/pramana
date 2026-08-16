"""Raw SQL for the `encounters` table.

No query here reads encounters back: nothing in this service's contract answers
questions about visits directly, only about notes that may reference one. This module
exists purely so seed_population has somewhere to write the rows notes.encounter_id
can point at."""

from datetime import date

import asyncpg

from member.models import Encounter

_COLUMNS = "id, member_id, date, description"


def _row_to_encounter(row: asyncpg.Record) -> Encounter:
    return Encounter(
        id=row["id"],
        member_id=row["member_id"],
        date=row["date"],
        description=row["description"],
    )


async def insert(conn, *, member_id: str, date: date, description: str) -> Encounter:
    row = await conn.fetchrow(
        f"""
        INSERT INTO encounters (member_id, date, description)
        VALUES ($1, $2, $3)
        RETURNING {_COLUMNS}
        """,
        member_id,
        date,
        description,
    )
    return _row_to_encounter(row)
