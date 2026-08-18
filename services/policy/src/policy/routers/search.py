"""The /search resource: hand the query to services.search and return its hits."""

from datetime import date

from fastapi import APIRouter, Request
from pramana_common.schemas import Hit
from pydantic import BaseModel, Field

from policy.domain.retrieval import CANDIDATES
from policy.services.search import search

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    date_of_service: date | None = None
    #: Bounded rather than free: a negative limit slices the ranked list from the end and
    #: quietly returns near-everything, and a limit above CANDIDATES promises more hits
    #: than fusion ever produces. Rejecting is better than answering something else.
    limit: int = Field(default=5, ge=1, le=CANDIDATES)


@router.post("/search")
async def search_endpoint(payload: SearchRequest, request: Request) -> list[Hit]:
    state = request.app.state
    async with state.pool.acquire() as conn:
        return await search(
            conn,
            state.embedder,
            state.reranker,
            payload.query,
            payload.date_of_service,
            payload.limit,
        )
