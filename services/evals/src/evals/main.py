"""App assembly, lifespan and router registration -- nothing else."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from evals import db
from evals.config import get_settings
from evals.routers import eval_runs, golden_cases, health
from evals.services.adjudication_client import AdjudicationClient

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    await db.probe_fresh()
    app.state.pool = await db.pool()
    await db.run_migrations(app.state.pool, MIGRATIONS_DIR)

    # One client for the process, so a run reuses connections across its cases rather
    # than opening one per case. No global timeout: each call in
    # `AdjudicationClient` sets its own, because submitting a case and polling its status
    # are not the same wait.
    app.state.http = httpx.AsyncClient()
    app.state.adjudication = AdjudicationClient(app.state.http, settings.adjudication_url)

    # Adjudication probed at startup: this service does nothing except drive it, so a
    # wrong address should fail here with the URL in the message rather than halfway
    # through a run that has already spent model tokens.
    try:
        await app.state.http.get(f"{settings.adjudication_url}/health", timeout=5.0)
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"adjudication unreachable at {settings.adjudication_url}: {exc}"
        ) from exc

    yield

    await app.state.http.aclose()
    await app.state.pool.close()


app = FastAPI(title="pramana-evals", lifespan=lifespan)
app.include_router(health.router)
app.include_router(golden_cases.router)
app.include_router(eval_runs.router)
