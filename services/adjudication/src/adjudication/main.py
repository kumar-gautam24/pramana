"""App assembly, the lifespan, and router registration -- nothing else. Every route,
orchestration and query lives in routers/, services/, repositories/ or domain/."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from adjudication import db, startup
from adjudication.routers import cases, events, health


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Probed before the pool opens: pool() itself opens no connection (min_size=0, see
    # db.py), so a wrong DATABASE_URL would otherwise stay invisible until the first
    # query runs. This probe is what makes misconfiguration a startup failure instead of
    # a 500 on the first request.
    await db.probe_fresh()
    app.state.pool = await db.pool()

    # `db.get_settings` rather than importing `config.get_settings` fresh here: it is
    # already the one seam every test monkeypatches to point this process at a test
    # database (see tests/test_health.py), and reusing it means that one patch covers
    # every probe below too, rather than each needing its own.
    settings = db.get_settings()

    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    await startup.probe_redis(redis_client)
    app.state.redis = redis_client

    # Scoped to startup only, not app.state: unlike `redis_client` above, nothing
    # after boot needs an httpx client -- POST/GET /cases never call policy or member
    # directly (only worker.py's pipeline run does that) -- so this one exists only
    # long enough to run the two probes below.
    async with httpx.AsyncClient() as http_client:
        await startup.probe_upstream(http_client, settings.policy_url, "policy")
        await startup.probe_upstream(http_client, settings.member_url, "member")

        if settings.probe_llm_on_startup:
            await startup.probe_llm(settings, http_client)

        yield

    await redis_client.aclose()
    await app.state.pool.close()


app = FastAPI(title="pramana adjudication", lifespan=lifespan)
app.include_router(health.router)
app.include_router(cases.router)
app.include_router(events.router)
