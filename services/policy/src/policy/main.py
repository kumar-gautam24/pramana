from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from policy.cms import fetch_ncd
from policy.config import get_settings
from policy.db import SessionFactory
from policy.embedding import Embedder
from policy.ingest import ingest_ncd


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Loading the model costs seconds. Paying it at startup keeps it off the first
    # caller's timeout, which is where it would otherwise land.
    app.state.embedder = Embedder()
    yield


app = FastAPI(title="pramana policy", lifespan=lifespan)


class IngestRequest(BaseModel):
    ncd_id: str


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}


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
