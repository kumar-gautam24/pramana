import os
from pathlib import Path

import pytest

from adjudication.db import pool, run_migrations

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

TEST_DATABASE_DSN = os.environ.get(
    "TEST_DATABASE_DSN",
    # Separate from pramana_adjudication: db_session rolls back writes but does not
    # isolate reads, so pointing tests at the real database would let a test see rows
    # it never inserted.
    "postgresql://pramana:pramana@localhost:5432/pramana_adjudication_test",
)


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
