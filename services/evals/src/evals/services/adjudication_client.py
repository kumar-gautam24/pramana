"""Typed wrapper over the two adjudication endpoints a run needs.

Injected `httpx.AsyncClient` for the same reasons the other services' clients take one: a
stub transport in tests, one connection pool per run, and no retries hidden here. A retry
in this layer would silently turn a rate-limited case into a slow one and the run would
report a latency it did not have."""

from dataclasses import dataclass
from typing import Any

import httpx


class AdjudicationUnavailable(Exception):
    """Adjudication did not answer, or answered with something unusable. A run records
    this against the case and carries on: one unreachable case is a gap in a measurement,
    not a reason to abandon the other fifty-nine."""


@dataclass(frozen=True)
class CaseState:
    status: str
    events: list[dict[str, Any]]


#: This service reaches `adjudication` directly rather than through the gateway, so it
#: states its own principal the way the gateway would have. Truthful rather than convenient:
#: `POST /api/eval-runs` is gated on `operator` at the front door, so every case submitted by
#: a run is an operator's act, and adjudication requires that role before it will accept the
#: `model_arithmetic` run mode.
#:
#: This is not a hole in the gateway's header stripping. `adjudication` is not reachable from
#: outside the private network at all -- the gateway exposes no route to it that this header
#: could be smuggled through, because it strips every inbound `x-pramana-` header first.
_PRINCIPAL_HEADERS = {"X-Pramana-Role": "operator"}


class AdjudicationClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    async def submit(self, fixture: dict[str, Any]) -> str:
        try:
            response = await self._client.post(
                f"{self._base_url}/cases",
                json=fixture,
                headers=_PRINCIPAL_HEADERS,
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise AdjudicationUnavailable(f"submit failed: {exc}") from exc

        if response.status_code // 100 != 2:
            raise AdjudicationUnavailable(
                f"submit answered {response.status_code}: {response.text[:200]}"
            )
        try:
            return response.json()["case_id"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AdjudicationUnavailable(f"submit returned no case_id: {exc}") from exc

    async def status(self, case_id: str) -> str:
        try:
            response = await self._client.get(
                f"{self._base_url}/cases/{case_id}", timeout=15.0
            )
            return response.json()["status"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise AdjudicationUnavailable(f"status failed: {exc}") from exc

    async def events(self, case_id: str) -> list[dict[str, Any]]:
        """The stored audit log. A run scores from this rather than from the SSE stream:
        the log is the authoritative record (ADR-0005), and reading the same data the
        commissioner would read is the point."""
        try:
            response = await self._client.get(
                f"{self._base_url}/cases/{case_id}/events", timeout=15.0
            )
            return response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise AdjudicationUnavailable(f"events failed: {exc}") from exc
