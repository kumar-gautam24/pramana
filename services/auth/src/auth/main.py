"""App assembly, lifespan and router registration -- nothing else.

Migrations run at startup rather than from a separate entrypoint: this service owns one
small schema, and a container that starts before its own tables exist has nothing useful
to do. Misconfiguration fails here, at boot, not on the first login."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from auth import db
from auth.routers import auth as auth_router
from auth.routers import health

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "migrations"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.probe_fresh()
    app.state.pool = await db.pool()
    await db.run_migrations(app.state.pool, MIGRATIONS_DIR)
    yield
    await app.state.pool.close()


app = FastAPI(title="pramana-auth", lifespan=lifespan)
app.include_router(health.router)
app.include_router(auth_router.router)
