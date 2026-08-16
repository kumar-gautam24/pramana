from contextlib import asynccontextmanager
from datetime import date

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pramana_common.schemas import Hit
from pydantic import BaseModel
from sqlalchemy import text

from policy.cms import fetch_ncd
from policy.config import get_settings
from policy.db import SessionFactory, engine
from policy.embedding import Embedder, Reranker
from policy.ingest import ingest_ncd
from policy.retrieval import search


async def _probe_database() -> None:
    """The cheapest query that proves the configured URL reaches a database that answers.
    Raises whatever the driver raises, so startup fails with the real cause."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Probed before the models, because this is the cheap check and the one that fails:
    # building the engine opens no connection, so a wrong DATABASE_URL stays invisible
    # until a query runs. This is what makes misconfiguration a startup failure instead
    # of a 500 on the first search.
    await _probe_database()

    # Loading the model costs seconds. Paying it at startup keeps it off the first
    # caller's timeout, which is where it would otherwise land.
    app.state.embedder = Embedder()
    app.state.reranker = Reranker()
    yield


app = FastAPI(title="pramana policy", lifespan=lifespan)


class IngestRequest(BaseModel):
    ncd_id: str


class SearchRequest(BaseModel):
    query: str
    date_of_service: date | None = None
    limit: int = 5


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is running. Deliberately touches no dependency -- a liveness
    probe that fails on a database blip gets the container restarted, which fixes
    nothing and drops the requests it was still able to serve."""
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: this instance can actually serve a search. The gateway's circuit breaker
    takes this as the signal to route traffic here, so it has to reflect the dependencies
    a search needs -- the database and the loaded models -- not just the process."""
    models = (getattr(app.state, "embedder", None), getattr(app.state, "reranker", None))
    if not all(models):
        return JSONResponse({"status": "unready", "reason": "models"}, status_code=503)

    try:
        await _probe_database()
    except Exception:
        # Any failure to reach the database is the same answer to the caller: do not send
        # this instance a search. The cause belongs in the logs, not in the probe body.
        return JSONResponse({"status": "unready", "reason": "database"}, status_code=503)

    return JSONResponse({"status": "ready"})


@app.post("/ingest")
async def ingest(request: IngestRequest) -> dict[str, int]:
    async with httpx.AsyncClient(
        base_url=get_settings().cms_base_url, timeout=30
    ) as client:
        records = await fetch_ncd(client, request.ncd_id)

    async with SessionFactory() as session:
        result = await ingest_ncd(session, app.state.embedder, records)
        await session.commit()

    return {
        "policies_added": result.policies_added,
        "chunks_added": result.chunks_added,
        "skipped": result.skipped,
    }


@app.post("/search")
async def search_endpoint(request: SearchRequest) -> list[Hit]:
    async with SessionFactory() as session:
        return await search(
            session,
            app.state.embedder,
            app.state.reranker,
            request.query,
            request.date_of_service,
            request.limit,
        )
