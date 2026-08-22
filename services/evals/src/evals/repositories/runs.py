"""Raw SQL for `eval_runs` and `eval_results`."""

import json
from typing import Any

import asyncpg
from pramana_common.criteria import Outcome

from evals.models.run import Ablation, EvalResult, EvalRun

_RUN_COLUMNS = (
    "id, model, prompt_version, thresholds, git_sha, ablation, status, started_at, finished_at"
)
_RESULT_COLUMNS = (
    "id, run_id, golden_case_id, case_id, outcome, reason, criterion_scores, error"
)


def _run(row: asyncpg.Record) -> EvalRun:
    return EvalRun(
        id=row["id"],
        model=row["model"],
        prompt_version=row["prompt_version"],
        thresholds=json.loads(row["thresholds"]),
        git_sha=row["git_sha"],
        ablation=Ablation(row["ablation"]),
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def _result(row: asyncpg.Record) -> EvalResult:
    return EvalResult(
        id=row["id"],
        run_id=row["run_id"],
        golden_case_id=row["golden_case_id"],
        case_id=None if row["case_id"] is None else str(row["case_id"]),
        outcome=None if row["outcome"] is None else Outcome(row["outcome"]),
        reason=row["reason"],
        criterion_scores=json.loads(row["criterion_scores"]),
        error=row["error"],
    )


async def insert_run(
    conn,
    *,
    model: str,
    prompt_version: str,
    thresholds: dict[str, Any],
    git_sha: str,
    ablation: Ablation,
) -> EvalRun:
    row = await conn.fetchrow(
        f"""
        INSERT INTO eval_runs (model, prompt_version, thresholds, git_sha, ablation)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING {_RUN_COLUMNS}
        """,
        model,
        prompt_version,
        json.dumps(thresholds),
        git_sha,
        ablation.value,
    )
    return _run(row)


async def finish_run(conn, run_id: int, status: str) -> None:
    await conn.execute(
        "UPDATE eval_runs SET status = $2, finished_at = now() WHERE id = $1", run_id, status
    )


async def get_run(conn, run_id: int) -> EvalRun | None:
    row = await conn.fetchrow(f"SELECT {_RUN_COLUMNS} FROM eval_runs WHERE id = $1", run_id)
    return None if row is None else _run(row)


async def list_runs(conn) -> list[EvalRun]:
    rows = await conn.fetch(f"SELECT {_RUN_COLUMNS} FROM eval_runs ORDER BY id DESC")
    return [_run(r) for r in rows]


async def upsert_result(
    conn,
    *,
    run_id: int,
    golden_case_id: int,
    case_id: str | None,
    outcome: Outcome | None,
    reason: str | None,
    criterion_scores: dict[str, Any],
    error: str | None,
) -> EvalResult:
    """ON CONFLICT rather than INSERT: a resumed run must correct the row it already
    wrote for a case, not add a second one -- see the UNIQUE constraint's comment in
    migration 0001 for why a duplicate would silently distort every rate."""
    row = await conn.fetchrow(
        f"""
        INSERT INTO eval_results
            (run_id, golden_case_id, case_id, outcome, reason, criterion_scores, error)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (run_id, golden_case_id) DO UPDATE SET
            case_id = EXCLUDED.case_id,
            outcome = EXCLUDED.outcome,
            reason = EXCLUDED.reason,
            criterion_scores = EXCLUDED.criterion_scores,
            error = EXCLUDED.error
        RETURNING {_RESULT_COLUMNS}
        """,
        run_id,
        golden_case_id,
        case_id,
        None if outcome is None else outcome.value,
        reason,
        json.dumps(criterion_scores),
        error,
    )
    return _result(row)


async def results_for_run(conn, run_id: int) -> list[EvalResult]:
    rows = await conn.fetch(
        f"SELECT {_RESULT_COLUMNS} FROM eval_results WHERE run_id = $1 ORDER BY golden_case_id",
        run_id,
    )
    return [_result(r) for r in rows]
