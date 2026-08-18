import os
from pathlib import Path

import pytest

from policy.db import migration_pool, pool, run_migrations

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

TEST_DATABASE_DSN = os.environ.get(
    "TEST_DATABASE_DSN",
    # A separate database from pramana_policy: db_session rolls back writes but does not
    # isolate reads, so against the real database the uniqueness check in ingest_ncd sees
    # the ingested corpus and skips records the tests expect to insert.
    "postgresql://pramana:pramana@localhost:5432/pramana_policy_test",
)


@pytest.fixture(scope="session")
async def db_pool():
    """Migrations run once per test session, not per test: they are schema, not the data
    a test owns, so they are not part of what db_session rolls back below."""
    mpool = await migration_pool(dsn=TEST_DATABASE_DSN)
    try:
        await run_migrations(mpool, MIGRATIONS_DIR)
    finally:
        await mpool.close()

    p = await pool(dsn=TEST_DATABASE_DSN)
    yield p
    await p.close()


@pytest.fixture
async def db_session(db_pool):
    """Each test runs inside a transaction that is rolled back, so tests never see each
    other's rows and the corpus is not left behind for the next run."""
    async with db_pool.acquire() as connection:
        transaction = connection.transaction()
        await transaction.start()
        try:
            yield connection
        finally:
            await transaction.rollback()
