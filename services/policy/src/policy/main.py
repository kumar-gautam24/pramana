from contextlib import asynccontextmanager
from datetime import date

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pramana_common.schemas import Hit
from pydantic import BaseModel, Field

from policy import db
from policy.cms import fetch_ncd
from policy.config import get_settings
from policy.embedding import Embedder, Reranker
from policy.ingest import ingest_ncd
from policy.retrieval import CANDIDATES, search


async def _probe_database() -> None:
    """The cheapest query that proves the configured DATABASE_URL reaches a database
    that answers. A dedicated, throwaway pool -- not app.state.pool -- so this check
    behaves the same whether or not the app has finished, or even started, its own
    bootstrap; that is what makes it usable from both the lifespan and /ready."""
    probe_pool = await db.pool()
    try:
        await db.probe(probe_pool)
    finally:
        await probe_pool.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Probed before the models, because this is the cheap check and the one that fails:
    # pool() opens no connection (min_size=0), so a wrong DATABASE_URL stays invisible
    # until a query runs. This is what makes misconfiguration a startup failure instead
    # of a 500 on the first search.
    await _probe_database()
    app.state.pool = await db.pool()

    # Loading the model costs seconds. Paying it at startup keeps it off the first
    # caller's timeout, which is where it would otherwise land.
    app.state.embedder = Embedder()
    app.state.reranker = Reranker()
    yield
    await app.state.pool.close()


app = FastAPI(title="pramana policy", lifespan=lifespan)


class IngestRequest(BaseModel):
    ncd_id: str


class SearchRequest(BaseModel):
    query: str
    date_of_service: date | None = None
    #: Bounded rather than free: a negative limit slices the ranked list from the end and
    #: quietly returns near-everything, and a limit above CANDIDATES promises more hits
    #: than fusion ever produces. Rejecting is better than answering something else.
    limit: int = Field(default=5, ge=1, le=CANDIDATES)


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

    # One transaction for the whole batch: a failure partway through must leave neither
    # a policy without its chunks nor some records ingested and others silently dropped.
    async with app.state.pool.acquire() as conn, conn.transaction():
        result = await ingest_ncd(conn, app.state.embedder, records)

    return {
        "policies_added": result.policies_added,
        "chunks_added": result.chunks_added,
        "skipped": result.skipped,
    }


@app.post("/search")
async def search_endpoint(request: SearchRequest) -> list[Hit]:
    async with app.state.pool.acquire() as conn:
        return await search(
            conn,
            app.state.embedder,
            app.state.reranker,
            request.query,
            request.date_of_service,
            request.limit,
        )
