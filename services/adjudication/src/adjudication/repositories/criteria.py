"""Raw SQL for the `criteria` table -- the persisted form of `services/extract.py`'s
`ExtractedSet`/`ExtractedCriterion`.

`insert_many` is the only entry point: it returns `domain.criteria_sets.CriteriaSet`
objects directly, with the database-assigned ids `services/verify.verify` and
`domain.criteria_sets.aggregate` both need, rather than handing back bare `Criterion`
rows the pipeline would have to regroup by `set_ordinal` itself.

Re-adjudication (finding 1, fix round 1): `insert_many` deletes `case_id`'s existing
criteria before inserting the fresh set, so a second `adjudicate(case_id)` -- which
Task 8's at-least-once Redis stream guarantees will happen -- gets a clean run instead
of colliding with the first run's rows on `uq_criteria_case_set_ordinal`. The caller
(`services/pipeline.py`) runs the delete and every insert inside one transaction, so a
mid-loop failure rolls back to the *previous* run's rows rather than leaving a mix of
old and half-written new ones. `ON DELETE CASCADE` from `criteria` to
`criterion_results` takes the superseded run's results with it for free. Superseded
`determinations` rows are untouched -- they are the case's history, not its working
state -- so an old determination's `blocking` array may name a criterion id that no
longer resolves after a later run; that is an accepted cost of "delete-then-insert" over
versioning the attempt, and `determinations.created_at` still places the row correctly
in the case's history regardless."""

import json

import asyncpg
from pramana_common.criteria import CriterionType

from adjudication.domain.criteria_sets import CriteriaSet
from adjudication.models.criterion import Criterion
from adjudication.services.extract import ExtractedSet

_COLUMNS = (
    "id, case_id, set_ordinal, ordinal, text, type, params, source_chunk_id, source_display_id"
)


def _row_to_criterion(row: asyncpg.Record) -> Criterion:
    return Criterion(
        id=row["id"],
        case_id=str(row["case_id"]),
        set_ordinal=row["set_ordinal"],
        ordinal=row["ordinal"],
        text=row["text"],
        type=CriterionType(row["type"]),
        # No codec is registered for jsonb (see adjudication/db.py's docstring on why
        # this service registers none) so asyncpg hands the column back as the raw
        # JSON text; this is the one place that gets parsed back into a dict.
        params=json.loads(row["params"]),
        source_chunk_id=row["source_chunk_id"],
        source_display_id=row["source_display_id"],
    )


async def insert_many(conn, case_id: str, sets: list[ExtractedSet]) -> list[CriteriaSet]:
    """Persist every criterion in `sets` and return the same disjunctive-normal-form
    structure with database-assigned ids.

    One INSERT per row rather than a bulk statement: asyncpg's batch protocol has no
    RETURNING equivalent, and the pipeline needs the assigned id of every row back --
    the same tradeoff `policy.repositories.chunks.insert_many` makes, over a corpus of
    comparable size (a handful of criteria per case).

    `conn` must be a connection already inside the caller's transaction, not the bare
    pool -- see this module's docstring on why the delete below and every insert that
    follows must commit or roll back together."""
    await conn.execute("DELETE FROM criteria WHERE case_id = $1", case_id)

    criteria_sets = []
    for extracted_set in sets:
        criteria = []
        for extracted_criterion in extracted_set.criteria:
            row = await conn.fetchrow(
                f"""
                INSERT INTO criteria (
                    case_id, set_ordinal, ordinal, text, type, params,
                    source_chunk_id, source_display_id
                )
                VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)
                RETURNING {_COLUMNS}
                """,
                case_id,
                extracted_set.ordinal,
                extracted_criterion.ordinal,
                extracted_criterion.text,
                extracted_criterion.type.value,
                json.dumps(extracted_criterion.params),
                extracted_criterion.source_chunk_id,
                extracted_criterion.source_display_id,
            )
            criteria.append(_row_to_criterion(row))
        criteria_sets.append(CriteriaSet(ordinal=extracted_set.ordinal, criteria=tuple(criteria)))
    return criteria_sets
