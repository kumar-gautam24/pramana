from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from member.db import engine


async def _probe_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail at startup rather than on the first request: create_async_engine opens no
    # connection, so without this a wrong DATABASE_URL starts cleanly and 500s later.
    await _probe_database()
    yield


app = FastAPI(title="pramana member", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is running. Deliberately touches no dependency -- a liveness
    probe that fails on a transient database blip gets the container restarted, which
    fixes nothing."""
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    """Readiness: this instance can actually serve requests. Reflects the database
    dependency so a caller's circuit breaker routes traffic away from an instance that
    cannot reach it."""
    try:
        await _probe_database()
    except Exception:
        return JSONResponse({"status": "unready", "reason": "database"}, status_code=503)
    return JSONResponse({"status": "ready"})
