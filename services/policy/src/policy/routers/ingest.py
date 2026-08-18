"""The /ingest resource: fetch a CMS determination and hand it to services.ingest."""

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

from policy.config import get_settings
from policy.services.cms import fetch_ncd
from policy.services.ingest import ingest_ncd

router = APIRouter()


class IngestRequest(BaseModel):
    ncd_id: str


@router.post("/ingest")
async def ingest(payload: IngestRequest, request: Request) -> dict[str, int]:
    async with httpx.AsyncClient(
        base_url=get_settings().cms_base_url, timeout=30
    ) as client:
        records = await fetch_ncd(client, payload.ncd_id)

    # One transaction for the whole batch: a failure partway through must leave neither
    # a policy without its chunks nor some records ingested and others silently dropped.
    async with request.app.state.pool.acquire() as conn, conn.transaction():
        result = await ingest_ncd(conn, request.app.state.embedder, records)

    return {
        "policies_added": result.policies_added,
        "chunks_added": result.chunks_added,
        "skipped": result.skipped,
    }
