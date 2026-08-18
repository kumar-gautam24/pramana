"""Typed wrapper over policy's POST /search.

Injected `httpx.AsyncClient` rather than one constructed here: it lets tests swap in a
stub transport, and it lets the pipeline share one connection pool across a case instead
of opening one per upstream call. No retries -- a retry hidden in this layer would
triple a case's latency with nothing in the audit trail to say why (see
task-4-brief.md); if retrying is ever wanted, it belongs where it can be recorded."""

from datetime import date

import httpx
from pramana_common.schemas import Hit

from adjudication.services import upstream


class PolicyClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    async def search(self, query: str, date_of_service: date | None, limit: int) -> list[Hit]:
        path = "/search"
        response = await upstream.send(
            self._client,
            "policy",
            "POST",
            f"{self._base_url}{path}",
            json={
                "query": query,
                "date_of_service": date_of_service.isoformat() if date_of_service else None,
                "limit": limit,
            },
        )
        return upstream.parse(
            "policy", response, path, lambda body: [Hit.model_validate(hit) for hit in body]
        )
