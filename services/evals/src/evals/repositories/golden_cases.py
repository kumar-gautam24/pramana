"""Raw SQL for `golden_cases`."""

import json
from typing import Any

import asyncpg
from pramana_common.criteria import Outcome

from evals.models.golden_case import GoldenCase

_COLUMNS = "id, fixture, expected_outcome, expected_criteria, author, notes, created_at"


def _row(row: asyncpg.Record) -> GoldenCase:
    return GoldenCase(
        id=row["id"],
        fixture=json.loads(row["fixture"]),
        expected_outcome=Outcome(row["expected_outcome"]),
        expected_criteria=json.loads(row["expected_criteria"]),
        author=row["author"],
        notes=row["notes"],
        created_at=row["created_at"],
    )


async def insert(
    conn,
    *,
    fixture: dict[str, Any],
    expected_outcome: Outcome,
    expected_criteria: list[str],
    author: str,
    notes: str | None,
) -> GoldenCase:
    row = await conn.fetchrow(
        f"""
        INSERT INTO golden_cases (fixture, expected_outcome, expected_criteria, author, notes)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING {_COLUMNS}
        """,
        json.dumps(fixture),
        expected_outcome.value,
        json.dumps(expected_criteria),
        author,
        notes,
    )
    return _row(row)


async def list_all(conn) -> list[GoldenCase]:
    rows = await conn.fetch(f"SELECT {_COLUMNS} FROM golden_cases ORDER BY id")
    return [_row(r) for r in rows]


async def get(conn, case_id: int) -> GoldenCase | None:
    row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM golden_cases WHERE id = $1", case_id)
    return None if row is None else _row(row)
