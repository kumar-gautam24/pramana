import os
from pathlib import Path

import pytest
import redis.asyncio as redis

from adjudication.db import pool, run_migrations

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

TEST_DATABASE_DSN = os.environ.get(
    "TEST_DATABASE_DSN",
    # Separate from pramana_adjudication: db_session rolls back writes but does not
    # isolate reads, so pointing tests at the real database would let a test see rows
    # it never inserted.
    "postgresql://pramana:pramana@localhost:5432/pramana_adjudication_test",
)

#: The same Redis this dev environment's docker compose already runs (see
#: docker-compose.yml), host-mapped rather than a second instance stood up for tests:
#: there is no lighter-weight way to prove the Stream/Pub-Sub mechanics in worker.py
#: and repositories/case_events.py actually work than talking to a real Redis -- the
#: same posture TEST_DATABASE_DSN above takes with Postgres. Tests that use it name
#: their own stream/group/channel (a fresh uuid4 each time) rather than the production
#: constants in services/queue.py, since there is no per-test rollback for Redis the
#: way db_session gives Postgres.
TEST_REDIS_URL = os.environ.get(
    "TEST_REDIS_URL", "redis://:dev-redis-password@localhost:6380"
)


@pytest.fixture(scope="session")
def database_url() -> str:
    """The same database as `db_pool`, in the scheme `Settings.database_url` carries.

    Exposed as a fixture rather than left for each test to spell out, because a test that
    writes its own connection string writes the *real* database's name as often as not --
    and a test reading state it never inserted is the failure this suite is arranged to
    prevent. Going through here means the TEST_DATABASE_DSN override moves every test."""
    return TEST_DATABASE_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)


@pytest.fixture(scope="session")
async def db_pool():
    """Migrations run once per test session, not per test: they are schema, not the data
    a test owns, so they are not part of what db_session rolls back below.

    A single pool() serves both steps: like member and unlike policy, this service
    registers no codec (it stores no vectors), so there is no pre-extension state a
    codec-free migration_pool() would need to avoid -- see adjudication/db.py."""
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


@pytest.fixture
async def redis_client():
    """A real client against TEST_REDIS_URL. Closed after each test; the keys a test
    creates (its own uuid4-named stream/group/channel -- see TEST_REDIS_URL's own
    comment) are left behind rather than cleaned up, the same accepted cost
    tests/test_pipeline.py's module docstring documents for `case_events` rows: nothing
    a later test reads is scoped to another test's random name, so accumulation is
    harmless."""
    # decode_responses=True to match how main.py and worker.py both construct their
    # own clients -- str throughout is what worker.py's field access and
    # case_events.py's publish/subscribe are written against.
    client = redis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()
