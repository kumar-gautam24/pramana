"""Raw SQL for the `criterion_results` table -- the persisted form of
`services/verify.Verification`.

Distinct from `pramana_common.criteria.CriterionResult` (the wire shape `Verification.result`
already carries, and what `domain.criteria_sets.aggregate` consumes directly from memory,
never by reading it back from this table): this module persists the fuller local row for the
audit trail -- `tool` and `evidence` -- and is not on the path the gate decision is computed
from."""

import json

import asyncpg
from pramana_common.criteria import Verdict

from adjudication.models.criterion_result import CriterionResult
from adjudication.services.verify import Verification

_COLUMNS = "id, criterion_id, verdict, confidence, tool, evidence"


def _row_to_result(row: asyncpg.Record) -> CriterionResult:
    return CriterionResult(
        id=row["id"],
        criterion_id=row["criterion_id"],
        verdict=Verdict(row["verdict"]),
        confidence=row["confidence"],
        tool=row["tool"],
        evidence=json.loads(row["evidence"]),
    )


async def insert_many(
    conn, verifications: list[tuple[int, Verification]]
) -> list[CriterionResult]:
    """`verifications`: each criterion's database id (`Criterion.id`, an `int`) paired
    with the `Verification` `services/verify.verify` produced for it. One INSERT per
    row, RETURNING the assigned id -- same reasoning as `criteria.insert_many`."""
    inserted = []
    for criterion_id, verification in verifications:
        row = await conn.fetchrow(
            f"""
            INSERT INTO criterion_results (criterion_id, verdict, confidence, tool, evidence)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING {_COLUMNS}
            """,
            criterion_id,
            verification.result.verdict.value,
            verification.result.confidence,
            verification.tool,
            json.dumps(verification.evidence),
        )
        inserted.append(_row_to_result(row))
    return inserted
