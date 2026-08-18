"""App assembly, the lifespan, and router registration -- nothing else. Every route,
orchestration and query lives in routers/, services/, repositories/ or domain/."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from adjudication import db
from adjudication.routers import health


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Probed before the pool opens: pool() itself opens no connection (min_size=0, see
    # db.py), so a wrong DATABASE_URL would otherwise stay invisible until the first
    # query runs. This probe is what makes misconfiguration a startup failure instead of
    # a 500 on the first request.
    await db.probe_fresh()
    app.state.pool = await db.pool()
    yield
    await app.state.pool.close()


app = FastAPI(title="pramana adjudication", lifespan=lifespan)
app.include_router(health.router)
