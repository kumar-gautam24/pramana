from fastapi import APIRouter

from auth import db

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Liveness only -- deliberately no database call. A health check that fails on a
    database blip causes a restart that fixes nothing."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict:
    """Readiness, which does touch the database: a service that cannot reach its own
    database should not be sent traffic, which is the distinction /health does not make."""
    await db.probe_fresh()
    return {"status": "ready"}
