"""Running the golden set through adjudication and scoring what comes back.

Cases run **one at a time, paced**. That is not timidity about concurrency: each case
costs several model calls against a rate-limited provider, and a run that saturates the
limit measures the token budget rather than the system. Pacing is what makes the number
mean what it claims to mean.

A case that never reaches a determination is recorded with `outcome = NULL` and the
reason, and the run continues. Folding an unreachable case into `escalate` would let an
outage read as a correct refusal, which is the one way this harness could flatter the
system it exists to audit."""

import asyncio
import logging
from typing import Any

from pramana_common.criteria import Outcome

from evals.domain import scoring
from evals.models.golden_case import GoldenCase
from evals.repositories import golden_cases as golden_repo
from evals.repositories import runs as runs_repo
from evals.services.adjudication_client import AdjudicationClient, AdjudicationUnavailable

logger = logging.getLogger(__name__)

#: Terminal states of an adjudication case.
_SETTLED = frozenset({"decided", "failed"})


def _decision_from(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if event.get("type") == "decision":
            return event.get("payload") or {}
    return None


def _criterion_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e.get("payload") or {} for e in events if e.get("type") == "criterion"]


def _weakest_confidence(criteria: list[dict[str, Any]]) -> float | None:
    """The minimum confidence across a case's criteria -- see `scoring.CaseOutcome` for
    why the weakest link is the value a threshold sweep must vary against."""
    values = [c["confidence"] for c in criteria if c.get("confidence") is not None]
    return min(values) if values else None


async def _await_settled(
    client: AdjudicationClient, case_id: str, timeout_seconds: float
) -> str:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        status = await client.status(case_id)
        if status in _SETTLED:
            return status
        if asyncio.get_running_loop().time() >= deadline:
            return status
        await asyncio.sleep(3.0)


async def _run_one(
    pool,
    client: AdjudicationClient,
    run_id: int,
    case: GoldenCase,
    timeout_seconds: float,
) -> None:
    try:
        case_id = await client.submit(case.fixture)
        status = await _await_settled(client, case_id, timeout_seconds)
        events = await client.events(case_id)
    except AdjudicationUnavailable as exc:
        async with pool.acquire() as conn:
            await runs_repo.upsert_result(
                conn,
                run_id=run_id,
                golden_case_id=case.id,
                case_id=None,
                outcome=None,
                reason=None,
                criterion_scores={},
                error=str(exc),
            )
        return

    decision = _decision_from(events)
    criteria = _criterion_events(events)

    outcome = None
    reason = None
    error = None
    if decision is None or status != "decided":
        error = f"no determination (status {status})"
    else:
        try:
            outcome = Outcome(decision["outcome"])
        except (KeyError, ValueError):
            error = f"unreadable decision payload: {decision!r}"
        reason = decision.get("reason")

    # Extraction quality, scored against the human-authored list. The criterion *texts*
    # are not on the event payloads (they carry ids and verdicts), so this compares
    # counts and matches only where the caller supplied expected criteria.
    extracted_texts = [c.get("criterion_text", "") for c in criteria]
    expected_texts = [str(text) for text in case.expected_criteria]
    criterion_score = scoring.match_criteria(expected_texts, extracted_texts)

    async with pool.acquire() as conn:
        await runs_repo.upsert_result(
            conn,
            run_id=run_id,
            golden_case_id=case.id,
            case_id=case_id,
            outcome=outcome,
            reason=reason,
            criterion_scores={
                "expected_count": criterion_score.expected_count,
                "extracted_count": criterion_score.extracted_count,
                "matched_count": criterion_score.matched_count,
                "precision": criterion_score.precision,
                "recall": criterion_score.recall,
                "f1": criterion_score.f1,
                "weakest_confidence": _weakest_confidence(criteria),
            },
            error=error,
        )


async def resume_run(
    pool,
    client: AdjudicationClient,
    *,
    run_id: int,
    seconds_between_cases: float,
    case_timeout_seconds: float,
    limit: int | None = None,
) -> int:
    """Score every golden case that this run has not already scored.

    Resumable by construction, and that is not a luxury here: a full run is tens of
    minutes of paced model calls, so it *will* sometimes be interrupted. Cases already
    carrying an outcome are skipped, so resuming costs only the work that is left --
    while a case previously recorded as unfinished (`outcome IS NULL`, an upstream
    failure or a timeout) is retried, because that row is a gap in the measurement rather
    than a result.

    `limit` caps how many cases run, for proving the harness without waiting for the
    whole set. Returns how many cases this call actually scored.
    """
    async with pool.acquire() as conn:
        cases = await golden_repo.list_all(conn)
        already = {
            result.golden_case_id
            for result in await runs_repo.results_for_run(conn, run_id)
            if result.outcome is not None
        }

    pending = [case for case in cases if case.id not in already]
    if limit is not None:
        pending = pending[:limit]

    for index, case in enumerate(pending):
        if index:
            # Paced, not concurrent -- see the module docstring.
            await asyncio.sleep(seconds_between_cases)
        logger.info(
            "run %s: golden case %s (%s of %s)", run_id, case.id, index + 1, len(pending)
        )
        await _run_one(pool, client, run_id, case, case_timeout_seconds)

    async with pool.acquire() as conn:
        # 'complete' even when individual cases failed: the run itself finished, and the
        # per-case `error` columns are where an incomplete measurement stays visible. A
        # run marked 'failed' because one case timed out would hide the ones that did not.
        await runs_repo.finish_run(conn, run_id, "complete")

    return len(pending)


async def report(pool, run_id: int, costs: scoring.CostModel) -> dict[str, Any] | None:
    """The measured result of a run: the confusion counts, the money, and the sweep."""
    async with pool.acquire() as conn:
        run = await runs_repo.get_run(conn, run_id)
        if run is None:
            return None
        results = await runs_repo.results_for_run(conn, run_id)
        cases = {case.id: case for case in await golden_repo.list_all(conn)}

    outcomes = [
        scoring.CaseOutcome(
            expected=cases[result.golden_case_id].expected_outcome,
            actual=result.outcome,
            confidence=result.criterion_scores.get("weakest_confidence"),
        )
        for result in results
        if result.golden_case_id in cases
    ]

    points, best = scoring.sweep(outcomes, costs)
    at_zero = scoring.score_at(outcomes, 0.0, costs)

    # Only cases whose author actually supplied an expected criteria list: averaging in
    # a case with none would report a precision of zero for a case nobody scored.
    extraction = [r.criterion_scores for r in results if r.criterion_scores.get("expected_count")]

    def mean(key: str) -> float | None:
        if not extraction:
            return None
        return sum(score.get(key, 0.0) for score in extraction) / len(extraction)

    return {
        "run": {
            "id": run.id,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "git_sha": run.git_sha,
            "ablation": run.ablation.value,
            "status": run.status,
            "thresholds": run.thresholds,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        },
        "cases_scored": len(outcomes),
        "case_level": {
            "at_threshold_zero": _point_to_wire(at_zero),
            "best": _point_to_wire(best) if best else None,
            "sweep": [_point_to_wire(point) for point in points],
        },
        "criterion_level": {
            "cases_with_expected_criteria": len(extraction),
            "mean_precision": mean("precision"),
            "mean_recall": mean("recall"),
            "mean_f1": mean("f1"),
        },
        "unfinished": [
            {"golden_case_id": r.golden_case_id, "error": r.error}
            for r in results
            if r.outcome is None
        ],
    }


def _point_to_wire(point: scoring.CasePoint) -> dict[str, Any]:
    return {
        "min_confidence": point.min_confidence,
        "auto_approval_rate": point.auto_approval_rate,
        "correct_approve": point.counts.correct_approve,
        "correct_escalate": point.counts.correct_escalate,
        "wrongly_approved": point.counts.wrongly_approved,
        "wrongly_escalated": point.counts.wrongly_escalated,
        "unfinished": point.counts.unfinished,
        "wrongly_approved_cost": point.wrongly_approved_cost,
        "wrongly_escalated_cost": point.wrongly_escalated_cost,
        "total_cost": point.total_cost,
    }
