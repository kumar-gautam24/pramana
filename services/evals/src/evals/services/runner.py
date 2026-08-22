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

from evals.domain import comparison, scoring
from evals.models.golden_case import GoldenCase
from evals.models.run import Ablation, EvalResult
from evals.repositories import golden_cases as golden_repo
from evals.repositories import runs as runs_repo
from evals.services.adjudication_client import AdjudicationClient, AdjudicationUnavailable

logger = logging.getLogger(__name__)

#: Terminal states of an adjudication case.
_SETTLED = frozenset({"decided", "failed"})

#: `eval_runs.ablation` -> `cases.run_mode`. The two vocabularies differ by one word --
#: a run has "no ablation", a case is decided "deterministically" -- and this is the only
#: place they meet. Written as a table rather than an `if` so an `Ablation` member added
#: without a run mode is a `KeyError` at the moment the run starts, not a run that silently
#: adjudicates every case the ordinary way while its column claims otherwise (ADR-0021).
_RUN_MODE_FOR_ABLATION = {
    Ablation.NONE: "deterministic",
    Ablation.MODEL_ARITHMETIC: "model_arithmetic",
}

#: A criterion verified by the ablated arm carries this prefix on its `tool`
#: (`adjudication.services.verify.arithmetic.MODEL_TOOL_PREFIX`). Counting them is how a
#: report says how much of a run was *actually* ablated: `condition_codes` has no comparison
#: step to move to the model -- `member` filters by code in SQL, so the fetch is the
#: membership test -- and a partial ablation reported as a whole one would overstate the
#: experiment.
_MODEL_TOOL_PREFIX = "model_arithmetic:"

#: The tool a judgment criterion records. Excluded from the ablation denominator: judgment
#: criteria are model-decided in both arms, so counting them would make every run look
#: partly ablated.
_JUDGMENT_TOOL = "judgment"


def _decision_from(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The case's final `decision` event, not its first.

    `determinations_one_per_case` (adjudication migration 0006) means a case can hold only
    one determination, so in a healthy system there is exactly one of these and first and
    last agree. They did not before that migration, and the way they disagreed is why this
    reads from the end: a case that reached the gate on one run and exhausted its retry
    ladder on another emitted both decisions milliseconds apart, in an order nothing
    controlled. Measured 2026-08-22 on case dc06c6d6 -- an `upstream_unavailable`
    escalation at 12:22:05.345 and the real gate escalation at 12:22:05.670. Taking the
    first scored the race rather than the adjudication, and scored it as
    `insufficient_evidence`: an arm's whole run can come out `upstream_unavailable` that
    way, which reads as agreement with its ablated twin and is nothing of the kind.

    Reading from the end is the safer direction even with the constraint in place. A
    duplicate that somehow reappears is then scored as the case's settled outcome rather
    than as whatever transient failure happened to be recorded first."""
    for event in reversed(events):
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


def _ablation_coverage(criteria: list[dict[str, Any]]) -> dict[str, int]:
    """How many of this case's comparison-bearing criteria the ablated arm actually decided.

    Reported rather than assumed, because the ablation is genuinely partial and saying so is
    cheaper than a reader discovering it. In a run with `ablation = none` the second number
    is zero, which is the honest reading of that run too."""
    tools = [str(c.get("tool", "")) for c in criteria]
    comparisons = [tool for tool in tools if tool != _JUDGMENT_TOOL]
    return {
        "comparison_criteria": len(comparisons),
        "by_model_arithmetic": sum(
            1 for tool in comparisons if tool.startswith(_MODEL_TOOL_PREFIX)
        ),
    }


async def _run_one(
    pool,
    client: AdjudicationClient,
    run_id: int,
    case: GoldenCase,
    timeout_seconds: float,
    run_mode: str,
) -> None:
    try:
        # The run's own mode is added to the fixture here rather than stored on the golden
        # case: a golden case is a labelled input and must be submittable under either arm,
        # or the two arms would not be running the same set. `golden_cases` rejects a fixture
        # that carries `run_mode` itself for the same reason.
        case_id = await client.submit({**case.fixture, "run_mode": run_mode})
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
                **_ablation_coverage(criteria),
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
    ablation: Ablation,
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

    `ablation` is required rather than defaulted. It decides the `run_mode` every case is
    submitted under, and a default would let a run whose column says `model_arithmetic`
    quietly adjudicate every case the ordinary way -- a published figure measuring the
    opposite of what it is labelled.
    """
    run_mode = _RUN_MODE_FOR_ABLATION[ablation]
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
            "run %s: golden case %s (%s of %s), run_mode=%s",
            run_id,
            case.id,
            index + 1,
            len(pending),
            run_mode,
        )
        await _run_one(pool, client, run_id, case, case_timeout_seconds, run_mode)

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

    outcomes = list(_outcomes_by_case(results, cases).values())

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
        "run": run_summary(run),
        # The rates every money figure below is a multiple of, published with the figures
        # rather than left in this service's configuration. A cost is a count times a rate;
        # a reader who cannot see the rate cannot check the arithmetic, cannot tell a
        # measurement from an assumption, and cannot disagree with the assumption -- which
        # `config.py`'s own docstring says is the point of these being configuration.
        "costs": {
            "average_claim_amount": costs.average_claim_amount,
            "review_minutes": costs.review_minutes,
            "clinician_hourly_rate": costs.clinician_hourly_rate,
            "review_cost": costs.review_cost,
        },
        # How much of this run was really ablated. `condition_codes` criteria have no
        # comparison step to move to the model, so even a `model_arithmetic` run reports
        # fewer ablated criteria than it has comparisons -- stated, not left to be
        # discovered (ADR-0021).
        "ablation_coverage": _coverage_of(results),
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


def run_summary(run) -> dict[str, Any]:
    """The conditions a run was made under, which is what makes its numbers reproducible --
    and, in a comparison, what a reader checks before believing a delta. One function, so the
    report and the comparison cannot describe the same run differently."""
    return {
        "id": run.id,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "git_sha": run.git_sha,
        "ablation": run.ablation.value,
        "status": run.status,
        "thresholds": run.thresholds,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def _coverage_of(results: list[EvalResult]) -> dict[str, int]:
    """`ablation_coverage`, summed over whichever results are passed in -- a whole run for the
    report, or the shared subset for a comparison, where a count over all of a run's cases
    would not line up with the deltas beside it."""
    return {
        "comparison_criteria": sum(
            int(r.criterion_scores.get("comparison_criteria", 0) or 0) for r in results
        ),
        "by_model_arithmetic": sum(
            int(r.criterion_scores.get("by_model_arithmetic", 0) or 0) for r in results
        ),
    }


def _outcomes_by_case(
    results: list[EvalResult], cases: dict[int, GoldenCase]
) -> dict[int, scoring.CaseOutcome]:
    return {
        result.golden_case_id: scoring.CaseOutcome(
            expected=cases[result.golden_case_id].expected_outcome,
            actual=result.outcome,
            confidence=result.criterion_scores.get("weakest_confidence"),
        )
        for result in results
        if result.golden_case_id in cases
    }


async def compare(
    pool, run_id: int, against_id: int, costs: scoring.CostModel
) -> dict[str, Any] | None:
    """Two runs side by side, and — only if they are an ablation pair — the difference.

    This is the endpoint the ablation exists for. Two report pages diffed by eye is not a
    measurement; a signed delta with the conditions of both runs printed above it is.

    Everything numeric is computed over the golden cases **both** runs decided, because a run
    that timed out on its two hardest cases would otherwise look cheaper than the run that
    finished them. Everything is scored at threshold zero, because that is the one operating
    point both arms were actually run at — the per-run sweeps are still on each run's own page.

    Returns `None` when either run does not exist, so the router can 404. It does not raise on
    an incomparable pair: both runs' own figures are valid and are returned, and only `delta`
    is withheld. See `domain/comparison.py` for why withholding it is the point rather than a
    limitation.
    """
    async with pool.acquire() as conn:
        first = await runs_repo.get_run(conn, run_id)
        second = await runs_repo.get_run(conn, against_id)
        if first is None or second is None:
            return None
        cases = {case.id: case for case in await golden_repo.list_all(conn)}
        results = {
            first.id: await runs_repo.results_for_run(conn, first.id),
            second.id: await runs_repo.results_for_run(conn, second.id),
        }

    pairing = comparison.pair(first, second)
    baseline_all = _outcomes_by_case(results[pairing.baseline.id], cases)
    ablated_all = _outcomes_by_case(results[pairing.ablated.id], cases)
    shared = sorted(set(baseline_all) & set(ablated_all))

    baseline_point = scoring.score_at([baseline_all[i] for i in shared], 0.0, costs)
    ablated_point = scoring.score_at([ablated_all[i] for i in shared], 0.0, costs)

    def coverage(run_id_: int) -> dict[str, int]:
        return _coverage_of([r for r in results[run_id_] if r.golden_case_id in shared])

    return {
        "baseline": run_summary(pairing.baseline),
        "ablated": run_summary(pairing.ablated),
        "comparable": pairing.comparable,
        # Named fields rather than a boolean, so a reader is told what to fix. An empty list
        # alongside `comparable: false` means the two are not an ablation pair at all.
        "differs_in": list(pairing.differs_in),
        "not_a_pair": pairing.not_a_pair,
        "costs": {
            "average_claim_amount": costs.average_claim_amount,
            "review_minutes": costs.review_minutes,
            "clinician_hourly_rate": costs.clinician_hourly_rate,
            "review_cost": costs.review_cost,
        },
        "shared_cases": len(shared),
        # Not silently dropped: a case only one arm reached is the most likely explanation
        # for a delta that looks surprising.
        "only_in_baseline": sorted(set(baseline_all) - set(ablated_all)),
        "only_in_ablated": sorted(set(ablated_all) - set(baseline_all)),
        "case_level": {
            "baseline": _point_to_wire(baseline_point),
            "ablated": _point_to_wire(ablated_point),
            # Withheld, never zeroed, when the two runs differ in more than their ablation:
            # a delta is a claim about causation, and there is none to make across three
            # simultaneous changes.
            "delta": (
                _delta_to_wire(comparison.delta(baseline_point, ablated_point))
                if pairing.comparable
                else None
            ),
        },
        "ablation_coverage": {
            "baseline": coverage(pairing.baseline.id),
            "ablated": coverage(pairing.ablated.id),
        },
        "disagreements": [
            {
                "golden_case_id": item.golden_case_id,
                "expected": item.expected.value,
                "baseline": None if item.baseline is None else item.baseline.value,
                "ablated": None if item.ablated is None else item.ablated.value,
            }
            for item in comparison.disagreements(
                {case_id: cases[case_id].expected_outcome for case_id in shared},
                baseline_all,
                ablated_all,
            )
        ],
    }


def _delta_to_wire(value: comparison.Delta) -> dict[str, Any]:
    return {
        "correct_approve": value.correct_approve,
        "correct_escalate": value.correct_escalate,
        "wrongly_approved": value.wrongly_approved,
        "wrongly_escalated": value.wrongly_escalated,
        "unfinished": value.unfinished,
        "auto_approval_rate": value.auto_approval_rate,
        "wrongly_approved_cost": value.wrongly_approved_cost,
        "wrongly_escalated_cost": value.wrongly_escalated_cost,
        "total_cost": value.total_cost,
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
