"""App assembly, the lifespan, and router registration -- nothing else. Every route,
orchestration and query lives in routers/, services/, repositories/ or domain/."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from policy import db
from policy.routers import health, ingest, search
from policy.services.embedding import Embedder, Reranker


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Probed before the pool opens: pool() itself opens no connection (min_size=0, see
    # db.py), so a wrong DATABASE_URL would otherwise stay invisible until the first
    # query runs. This probe is what makes misconfiguration a startup failure instead of
    # a 500 on the first search.
    await db.probe_fresh()
    app.state.pool = await db.pool()

    # Loading the model costs seconds. Paying it at startup keeps it off the first
    # caller's timeout, which is where it would otherwise land.
    app.state.embedder = Embedder()
    app.state.reranker = Reranker()
    yield
    await app.state.pool.close()


app = FastAPI(title="pramana policy", lifespan=lifespan)
app.include_router(health.router)
app.include_router(ingest.router)
app.include_router(search.router)
