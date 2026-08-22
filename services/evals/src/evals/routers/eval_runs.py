"""The eval-run resource: starting a run and reading its measured result.

A run is started in the background and its id returned immediately. It takes minutes --
each case is several model calls against a rate-limited provider, paced deliberately (see
`services/runner.py`) -- and an HTTP request held open for that long would be a timeout
waiting to happen, on the one route whose output is a legal obligation."""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pramana_common.gate import GateThresholds
from pydantic import BaseModel, Field

from evals.config import get_settings
from evals.domain.scoring import CostModel
from evals.models.run import Ablation
from evals.repositories import runs as runs_repo
from evals.services import runner

logger = logging.getLogger(__name__)

router = APIRouter()


class RunIn(BaseModel):
    #: The confidence bar the sweep is centred on. The run records it, and the report
    #: sweeps around it, so a run is reproducible from its own row.
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ablation: Ablation = Ablation.NONE
    #: Cap the number of cases. A full run takes tens of minutes against a rate-limited
    #: provider; a capped run proves the harness end to end without that wait, and is
    #: recorded distinctly so it cannot be mistaken for a full one.
    limit: int | None = Field(default=None, ge=1)


def _costs() -> CostModel:
    settings = get_settings()
    return CostModel(
        average_claim_amount=settings.average_claim_amount,
        review_minutes=settings.review_minutes,
        clinician_hourly_rate=settings.clinician_hourly_rate,
    )


@router.post("/eval-runs", status_code=202)
async def start(body: RunIn, request: Request, background: BackgroundTasks) -> dict:
    """Start a run in the background and return its id.

    `ablation` answered 501 until 2026-08-22, because the mode it names did not exist in
    `adjudication` and a run that quietly ignored the flag would have published a figure
    labelled "model arithmetic" that SQL produced -- the exact opposite of what ADR-0003
    wants proven. It exists now (ADR-0021): `runner` submits each case with the matching
    `run_mode`, so the column and the cases it describes cannot disagree."""
    settings = get_settings()

    thresholds = {"min_confidence": GateThresholds(body.min_confidence).min_confidence}

    async with request.app.state.pool.acquire() as conn:
        run = await runs_repo.insert_run(
            conn,
            model=settings.model_name,
            prompt_version=settings.prompt_version,
            thresholds=thresholds,
            git_sha=settings.git_sha,
            ablation=body.ablation,
        )

    async def execute() -> None:
        try:
            await runner.resume_run(
                request.app.state.pool,
                request.app.state.adjudication,
                run_id=run.id,
                seconds_between_cases=settings.seconds_between_cases,
                case_timeout_seconds=settings.case_timeout_seconds,
                limit=body.limit,
                ablation=run.ablation,
            )
        except Exception:
            logger.exception("eval run %s failed", run.id)
            async with request.app.state.pool.acquire() as conn:
                await runs_repo.finish_run(conn, run.id, "failed")

    background.add_task(execute)
    return {"run_id": run.id, "status": "running"}


@router.get("/eval-runs")
async def list_runs(request: Request) -> list[dict]:
    async with request.app.state.pool.acquire() as conn:
        runs = await runs_repo.list_runs(conn)
    return [runner.run_summary(run) for run in runs]


@router.get("/eval-runs/{run_id}")
async def get_run(run_id: int, request: Request) -> dict:
    """The measured result: confusion counts, the money each error costs, the threshold
    sweep, and extraction precision and recall."""
    report = await runner.report(request.app.state.pool, run_id, _costs())
    if report is None:
        raise HTTPException(status_code=404, detail="no such run")
    return report


@router.get("/eval-runs/{run_id}/comparison")
async def compare_runs(run_id: int, against: int, request: Request) -> dict:
    """This run beside another, and — only if the two are an ablation pair — the difference.

    The endpoint the ablation exists for. Two report pages diffed by eye is not a measurement;
    a signed delta with the conditions of both runs printed above it is (ADR-0021).

    `against` is required and has no default. There is no "compare with the obvious other
    one": picking a twin by heuristic is exactly how a comparison ends up being made between
    runs that differ in three things, and the caller naming both is what makes the pair a
    deliberate claim. Orientation is read off each run's `ablation`, not off which id is in
    the path, so the answer does not depend on which way round they are named.

    Answers 200 even for a pair that is not comparable: both runs' own figures are valid and
    are returned, and only the delta is withheld. A 4xx would be the wrong shape — nothing
    about the *request* was malformed, and the reason the delta is missing is a fact about the
    two runs that the caller needs to read."""
    if against == run_id:
        raise HTTPException(
            status_code=422, detail="a run cannot be compared against itself"
        )
    result = await runner.compare(request.app.state.pool, run_id, against, _costs())
    if result is None:
        raise HTTPException(status_code=404, detail="no such run")
    return result
