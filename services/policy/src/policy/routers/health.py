"""Liveness and readiness: the two probes the gateway's circuit breaker depends on."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from policy import db

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is running. Deliberately touches no dependency -- a liveness
    probe that fails on a database blip gets the container restarted, which fixes
    nothing and drops the requests it was still able to serve."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Readiness: this instance can actually serve a search. The gateway's circuit breaker
    takes this as the signal to route traffic here, so it has to reflect the dependencies
    a search needs -- the database and the loaded models -- not just the process."""
    state = request.app.state
    models = (getattr(state, "embedder", None), getattr(state, "reranker", None))
    if not all(models):
        return JSONResponse({"status": "unready", "reason": "models"}, status_code=503)

    try:
        await db.probe_fresh()
    except Exception:
        # Any failure to reach the database is the same answer to the caller: do not send
        # this instance a search. The cause belongs in the logs, not in the probe body.
        return JSONResponse({"status": "unready", "reason": "database"}, status_code=503)

    return JSONResponse({"status": "ready"})
