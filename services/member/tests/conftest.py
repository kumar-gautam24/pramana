import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from member.db import pool, run_migrations

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

TEST_DATABASE_DSN = os.environ.get(
    "TEST_DATABASE_DSN",
    # Separate from pramana_member: db_session rolls back writes but does not isolate
    # reads, so pointing tests at the database generate.py seeds would let a test see
    # rows it never inserted (plan 02 shipped exactly this bug once).
    "postgresql://pramana:pramana@localhost:5432/pramana_member_test",
)


@pytest.fixture(scope="session")
async def db_pool():
    """Migrations run once per test session, not per test: they are schema, not the data
    a test owns, so they are not part of what db_session rolls back below.

    A single pool() serves both steps: unlike policy, member's pool() registers no
    codec (member has no vector column), so there is no pre-extension state a
    codec-free migration_pool() would need to avoid -- see member/db.py."""
    p = await pool(dsn=TEST_DATABASE_DSN)
    await run_migrations(p, MIGRATIONS_DIR)
    yield p
    await p.close()


@pytest.fixture
async def db_session(db_pool):
    """Each test runs inside a transaction that is rolled back, so tests never see each
    other's rows and nothing is left behind for the next run."""
    async with db_pool.acquire() as connection:
        transaction = connection.transaction()
        await transaction.start()
        try:
            yield connection
        finally:
            await transaction.rollback()


class _SingleConnectionPool:
    """Stands in for `app.state.pool` so a route function can be called directly
    (`await members.coverage(...)`) and see rows `db_session` inserted, without a route
    test ever touching the real database. `acquire()` hands back the one connection
    this fixture already opened -- the same seam `main.SessionFactory` monkeypatching
    used to give a route function the test's own rolled-back transaction, before that
    was a SQLAlchemy session and this is an asyncpg connection."""

    def __init__(self, connection):
        self._connection = connection

    def acquire(self):
        return self

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, *exc_info):
        return False


@pytest.fixture
async def routed_session(db_pool, monkeypatch):
    from member.main import app

    async with db_pool.acquire() as connection:
        transaction = connection.transaction()
        await transaction.start()
        # raising=False: app.state.pool only exists once the lifespan has run, and this
        # fixture calls route functions directly without going through it.
        monkeypatch.setattr(app.state, "pool", _SingleConnectionPool(connection), raising=False)
        try:
            yield connection
        finally:
            await transaction.rollback()


@pytest.fixture
def fake_request(routed_session):
    """A stand-in for FastAPI's `Request` with just enough shape (`.app.state.pool`) for
    a router function pulled out of the app's dependency injection to resolve its pool
    the same way it would inside a real request. Depends on `routed_session` so the pool
    it exposes is already the rolled-back, single-connection one patched above."""
    from member.main import app

    return SimpleNamespace(app=app)
