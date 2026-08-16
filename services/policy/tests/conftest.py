import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from policy.models import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    # A separate database from pramana_policy: db_session rolls back writes but does not
    # isolate reads, so against the real database the uniqueness check in ingest_ncd sees
    # the ingested corpus and skips records the tests expect to insert.
    "postgresql+asyncpg://pramana:pramana@localhost:5433/pramana_policy_test",
)


@pytest.fixture(scope="session")
def engine():
    return create_async_engine(TEST_DATABASE_URL)


@pytest.fixture
async def db_session(engine):
    """Each test runs inside a transaction that is rolled back, so tests never see each
    other's rows and the corpus is not left behind for the next run."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with factory() as session:
            yield session
        await transaction.rollback()
