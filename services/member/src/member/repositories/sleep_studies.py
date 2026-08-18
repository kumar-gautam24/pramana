"""Raw SQL for the `sleep_studies` table."""

from datetime import date

import asyncpg

from member.models import SleepStudy

_COLUMNS = "id, member_id, date, test_type, channels, apnea_events, recorded_hours, ahi"


def _row_to_sleep_study(row: asyncpg.Record) -> SleepStudy:
    return SleepStudy(
        id=row["id"],
        member_id=row["member_id"],
        date=row["date"],
        test_type=row["test_type"],
        channels=row["channels"],
        apnea_events=row["apnea_events"],
        recorded_hours=row["recorded_hours"],
        ahi=row["ahi"],
    )


async def insert(
    conn,
    *,
    member_id: str,
    date: date,
    test_type: str,
    channels: int,
    apnea_events: int,
    recorded_hours: float,
    ahi: float,
) -> SleepStudy:
    row = await conn.fetchrow(
        f"""
        INSERT INTO sleep_studies
            (member_id, date, test_type, channels, apnea_events, recorded_hours, ahi)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_COLUMNS}
        """,
        member_id,
        date,
        test_type,
        channels,
        apnea_events,
        recorded_hours,
        ahi,
    )
    return _row_to_sleep_study(row)


async def sleep_studies_before(conn, member_id: str, on: date) -> list[SleepStudy]:
    """Studies on or before `on`. A study performed after the date of service cannot
    justify coverage on it, so `on` is inclusive but nothing later is."""
    rows = await conn.fetch(
        f"SELECT {_COLUMNS} FROM sleep_studies WHERE member_id = $1 AND date <= $2 "
        "ORDER BY date",
        member_id,
        on,
    )
    return [_row_to_sleep_study(row) for row in rows]
