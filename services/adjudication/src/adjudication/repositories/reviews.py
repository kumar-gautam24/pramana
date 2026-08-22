"""Raw SQL for `reviews`.

This is the one table in this service whose `outcome` is not constrained to
approve/escalate, and that is the entire reason it is a separate table from
`determinations`: a licensed clinician *may* issue an adverse determination, and the
machine may not (ADR-0002). The column is still open pending the vocabulary plan 07
settles -- recorded as deliberate, not forgotten."""

import asyncpg

from adjudication.models.review import Review

_COLUMNS = "id, case_id, clinician_id, outcome, rationale, agreed_with_system, created_at"


def _row_to_review(row: asyncpg.Record) -> Review:
    return Review(
        id=row["id"],
        case_id=str(row["case_id"]),
        clinician_id=row["clinician_id"],
        outcome=row["outcome"],
        rationale=row["rationale"],
        agreed_with_system=row["agreed_with_system"],
        created_at=row["created_at"],
    )


async def insert(
    conn,
    *,
    case_id: str,
    clinician_id: str,
    outcome: str,
    rationale: str,
    agreed_with_system: bool,
) -> Review:
    row = await conn.fetchrow(
        f"""
        INSERT INTO reviews (case_id, clinician_id, outcome, rationale, agreed_with_system)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING {_COLUMNS}
        """,
        case_id,
        clinician_id,
        outcome,
        rationale,
        agreed_with_system,
    )
    return _row_to_review(row)


async def list_for_case(conn, case_id: str) -> list[Review]:
    rows = await conn.fetch(
        f"SELECT {_COLUMNS} FROM reviews WHERE case_id = $1 ORDER BY created_at, id",
        case_id,
    )
    return [_row_to_review(row) for row in rows]
