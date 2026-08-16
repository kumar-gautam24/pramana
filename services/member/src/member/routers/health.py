"""Liveness and readiness: the two probes the gateway's circuit breaker depends on."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from member import db

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is running. Deliberately touches no dependency -- a liveness
    probe that fails on a transient database blip gets the container restarted, which
    fixes nothing."""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: this instance can actually serve requests. Reflects the database
    dependency so a caller's circuit breaker routes traffic away from an instance that
    cannot reach it."""
    try:
        await db.probe_fresh()
    except Exception:
        return JSONResponse({"status": "unready", "reason": "database"}, status_code=503)
    return JSONResponse({"status": "ready"})
