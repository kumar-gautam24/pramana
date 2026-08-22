"""Raw SQL for the `determinations` table.

`blocking` holds one of two shapes, and this module makes no attempt to tell them
apart -- only the pipeline that wrote the row knows which one it is:

- On an ordinary escalation (the case reached the gate), it is `AggregateDecision.blocking`
  as-is: the closest set's unmet criterion ids, each a `str(Criterion.id)`.
- On a short-circuit (task-7 brief, decision 2 -- not_eligible, no_governing_policy,
  no_criteria, or upstream_unavailable), the case never reached the gate at all, so
  there are no criterion ids to report. `blocking` instead holds a single-element JSON
  array naming the short-circuit itself, e.g. `["not_eligible"]`.

Both shapes are lists of strings, so the column and this module's signature don't need
to distinguish them -- but a reader of the raw table cannot either, and that is exactly
the gap this docstring exists to close. Without it, a determination row with `reason =
"insufficient_evidence"` is ambiguous between "member has no coverage record" (
not_eligible) and "the model could not answer" (upstream_unavailable) -- a distinction
a state insurance commissioner reading this table needs and the `case_events` log (see
`services/pipeline.py`) is the only place a reader can otherwise get it from. The
`case_events` `decision` payload carries the same short-circuit name for that reason."""

import json

import asyncpg
from pramana_common.criteria import GateReason, Outcome

from adjudication.models.determination import Determination

_COLUMNS = "id, case_id, outcome, reason, blocking, thresholds, winning_set, created_at"


def _row_to_determination(row: asyncpg.Record) -> Determination:
    return Determination(
        id=row["id"],
        case_id=str(row["case_id"]),
        outcome=Outcome(row["outcome"]),
        reason=GateReason(row["reason"]) if row["reason"] is not None else None,
        blocking=json.loads(row["blocking"]),
        thresholds=json.loads(row["thresholds"]),
        winning_set=row["winning_set"],
        created_at=row["created_at"],
    )


async def insert(
    conn,
    *,
    case_id: str,
    outcome: Outcome,
    reason: GateReason | None,
    blocking: list[str],
    thresholds: dict,
    winning_set: int | None,
) -> Determination:
    row = await conn.fetchrow(
        f"""
        INSERT INTO determinations (case_id, outcome, reason, blocking, thresholds, winning_set)
        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
        RETURNING {_COLUMNS}
        """,
        case_id,
        outcome.value,
        reason.value if reason is not None else None,
        json.dumps(blocking),
        json.dumps(thresholds),
        winning_set,
    )
    return _row_to_determination(row)


async def latest(conn, case_id: str) -> Determination | None:
    """The determination this case already reached, or `None` if it has not reached one.

    Ordered by `id` rather than `created_at` because two determinations written inside the
    same clock tick must still have a defined "later" -- and a case that has more than one
    row here is precisely the corruption `pipeline.adjudicate`'s re-entry guard exists to
    stop, so this function has to stay well-defined while that is still possible."""
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM determinations WHERE case_id = $1 ORDER BY id DESC LIMIT 1",
        case_id,
    )
    return _row_to_determination(row) if row is not None else None
