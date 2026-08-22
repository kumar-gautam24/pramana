"""Startup probes beyond the database (see `db.probe_fresh`): Redis, the two upstream
services, and ADR-0010's model-output guard ("services refuse to start if the
configured model cannot produce schema-constrained output").

Each function here raises on failure and nothing in `main.py`'s lifespan or
`worker.py`'s `main()` catches that -- the whole point is to crash the process at boot
rather than surface as a 500, or as a case stuck `running`, on whichever request or
case happens to need the broken dependency first."""

import httpx
from redis.asyncio import Redis

from adjudication.config import Settings
from adjudication.services.llm import build_provider


class StartupProbeError(Exception):
    """A dependency this service needs in order to run at all did not answer at boot.

    Deliberately not `UpstreamUnavailable`: that exception is what the *pipeline*
    catches mid-case and turns into a routine escalation (task-7 brief). Nothing here
    is mid-case -- there is no case yet -- so nothing should catch this one. It is
    meant to propagate out of the lifespan and crash the process."""


async def probe_redis(redis_client: Redis) -> None:
    """The cheapest proof `redis_client` reaches a Redis that answers -- the same role
    `db.probe` plays for Postgres."""
    try:
        await redis_client.ping()
    except Exception as exc:
        raise StartupProbeError(f"redis unavailable: {exc}") from exc


async def probe_upstream(client: httpx.AsyncClient, base_url: str, name: str) -> None:
    """`base_url`'s own liveness probe (`/health`, the same endpoint
    `routers/health.py` exposes here) -- not `/ready`, which would couple this
    service's own boot to policy's or member's database being reachable, a failure this
    service cannot act on and has no reason to refuse to start over."""
    try:
        response = await client.get(f"{base_url}/health", timeout=5.0)
    except httpx.HTTPError as exc:
        raise StartupProbeError(f"{name} unavailable: {exc}") from exc
    if response.status_code != 200:
        raise StartupProbeError(f"{name} unavailable: status {response.status_code}")


#: Small enough to say nothing about the domain -- this probe exists to prove the
#: configured model can follow *a* JSON Schema at all, not to test extraction quality
#: (extract.py's own schema, and extraction's own accuracy, are covered by
#: tests/test_extract.py and tests/test_pipeline.py). Any model that cannot return
#: {"ok": true} on request cannot be trusted with the real one either.
_PROBE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
_PROBE_MESSAGES = [
    {"role": "user", "content": 'Reply with exactly the JSON object {"ok": true}.'}
]


async def probe_llm(settings: Settings, client: httpx.AsyncClient) -> None:
    """ADR-0010's guard. Builds the configured provider the same way `worker.py` does
    and asks it for schema-constrained output; a provider that cannot reach its
    endpoint, cannot honour the schema, or answers with something that doesn't even
    parse as the requested shape all raise `StartupProbeError` here rather than being
    discovered on the first real case."""
    provider = build_provider(settings, client)
    try:
        answer = await provider.chat(_PROBE_MESSAGES, _PROBE_SCHEMA)
    except Exception as exc:
        raise StartupProbeError(
            f"configured model ({settings.llm_provider}) cannot produce "
            f"schema-constrained output: {exc}"
        ) from exc
    if not isinstance(answer, dict) or "ok" not in answer:
        raise StartupProbeError(
            f"configured model ({settings.llm_provider}) returned {answer!r}, "
            "not the requested schema-constrained output"
        )
