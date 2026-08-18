"""Raw SQL for the `cpap_usage` table, plus the one fact this service derives from it:
how many nights in a window qualify against a caller-supplied hourly bar."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Adherence:
    #: Nights with any usage row in the window, qualifying or not -- the denominator a
    #: caller needs to see the 0/0 case for what it is rather than infer it from fraction.
    nights: int
    #: Counted against the caller's `min_hours`, not a threshold this service holds.
    qualifying_nights: int
    fraction: float


async def insert_many(conn, member_id: str, nights: list[tuple[date, float]]) -> int:
    """One executemany, not a RETURNING insert per row: unlike chunks (policy), nothing
    downstream needs the inserted rows back, only a count (see
    member.services.seed.SeedResult)."""
    await conn.executemany(
        "INSERT INTO cpap_usage (member_id, night, hours) VALUES ($1, $2, $3)",
        [(member_id, night, hours) for night, hours in nights],
    )
    return len(nights)


async def adherence(
    conn, member_id: str, start: date, end: date, min_hours: float
) -> Adherence:
    """Nights of CPAP usage within `[start, end]`, both bounds inclusive, and how many
    of them logged at least `min_hours`.

    `min_hours` is required rather than defaulted for the same reason `codes` is
    required on conditions_before: four hours a night is NCD 240.4's number, and a
    threshold written here would be this service deciding what the policy says. A
    default would read as the answer and be used as one -- so the caller that owns the
    policy states it (ADR-0003).

    The caller supplies the window too (adjudication computes the consecutive 30-day
    span from the date of service); this only counts what falls inside the bounds it's
    given, so usage outside the window can't inflate a member's qualifying-night count.
    """
    rows = await conn.fetch(
        "SELECT hours FROM cpap_usage WHERE member_id = $1 AND night >= $2 AND night <= $3",
        member_id,
        start,
        end,
    )
    nights = len(rows)
    qualifying_nights = sum(1 for row in rows if row["hours"] >= min_hours)
    # A member who never used the device must fail the criterion cleanly -- 0/0 is a
    # fact about the record, not a program error, so this returns 0.0 rather than
    # raising ZeroDivisionError and turning a refusal into a 500.
    fraction = qualifying_nights / nights if nights else 0.0
    return Adherence(nights=nights, qualifying_nights=qualifying_nights, fraction=fraction)
