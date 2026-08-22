from fastapi import APIRouter

from evals import db

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Liveness only -- no database call. A health check that fails on a database blip
    causes a restart that fixes nothing."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict:
    await db.probe_fresh()
    return {"status": "ready"}
