"""Raw SQL for the `notes` table.

Named `member.repositories.notes` to keep it distinct from `member.domain.notes`, the
note-text generator -- callers import both under a qualified name (`notes_repo` /
`notes_module`), never bare, so the two never collide."""

from datetime import date

import asyncpg

from member.models import Note

_COLUMNS = "id, member_id, encounter_id, date, text"


def _row_to_note(row: asyncpg.Record) -> Note:
    return Note(
        id=row["id"],
        member_id=row["member_id"],
        encounter_id=row["encounter_id"],
        date=row["date"],
        text=row["text"],
    )


async def insert(
    conn, *, member_id: str, date: date, text: str, encounter_id: int | None = None
) -> Note:
    row = await conn.fetchrow(
        f"""
        INSERT INTO notes (member_id, encounter_id, date, text)
        VALUES ($1, $2, $3, $4)
        RETURNING {_COLUMNS}
        """,
        member_id,
        encounter_id,
        date,
        text,
    )
    return _row_to_note(row)


async def notes_before(conn, member_id: str, on: date) -> list[Note]:
    """Notes on or before `on`, for the symptom-documentation judgment call the model
    still has to make (see member.domain.notes) -- this only bounds which notes are in
    scope."""
    rows = await conn.fetch(
        f"SELECT {_COLUMNS} FROM notes WHERE member_id = $1 AND date <= $2 ORDER BY date",
        member_id,
        on,
    )
    return [_row_to_note(row) for row in rows]
