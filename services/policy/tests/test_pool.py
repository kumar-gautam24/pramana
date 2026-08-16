"""pool() and probe() are the startup-time checks ADR-0013 requires to survive the
switch away from SQLAlchemy: a bad DATABASE_URL must fail before the service accepts
traffic, and policy's vector column must round-trip through asyncpg without a
hand-rolled codec."""

import uuid

import asyncpg
import pytest

from policy.db import migration_pool, pool, probe, run_migrations

ADMIN_DSN = "postgresql://pramana:pramana@localhost:5432/postgres"


def _scratch_dsn(name: str) -> str:
    return f"postgresql://pramana:pramana@localhost:5432/{name}"


@pytest.fixture
async def scratch_db():
    name = f"pramana_scratch_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(ADMIN_DSN)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()

    try:
        yield name
    finally:
        admin = await asyncpg.connect(ADMIN_DSN)
        try:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        finally:
            await admin.close()


async def test_probe_succeeds_against_a_reachable_database(scratch_db):
    # pool() always needs the vector extension present (its init callback registers
    # the codec on every connection), so this creates it directly rather than through
    # run_migrations() -- this test is only about probe(), and
    # test_bootstraps_a_clean_database_through_migration_pool_then_pool below is what
    # actually proves pool() and migration_pool() compose correctly on a database that
    # starts without the extension.
    admin = await asyncpg.connect(_scratch_dsn(scratch_db))
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
    finally:
        await admin.close()

    p = await pool(dsn=_scratch_dsn(scratch_db))
    try:
        await probe(p)
    finally:
        await p.close()


async def test_probe_raises_against_an_unreachable_database():
    p = await pool(dsn="postgresql://pramana:pramana@localhost:5432/pramana_does_not_exist")
    try:
        with pytest.raises(asyncpg.PostgresError):
            await probe(p)
    finally:
        await p.close()


async def test_pool_registers_the_vector_codec(scratch_db):
    """Without pgvector.asyncpg.register_vector, a vector column round-trips as an
    opaque string, and every query mapping a row to a dataclass has to parse it by
    hand. This is the one function call ADR-0013 says replaces the SQLAlchemy dialect."""
    admin = await asyncpg.connect(_scratch_dsn(scratch_db))
    try:
        await admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await admin.execute("CREATE TABLE embeddings (id integer PRIMARY KEY, v vector(3))")
    finally:
        await admin.close()

    p = await pool(dsn=_scratch_dsn(scratch_db))
    try:
        async with p.acquire() as conn:
            await conn.execute(
                "INSERT INTO embeddings (id, v) VALUES ($1, $2)", 1, [0.5, 1.0, 1.5]
            )
            row = await conn.fetchrow("SELECT v FROM embeddings WHERE id = 1")
        assert row["v"].to_list() == [0.5, 1.0, 1.5]
    finally:
        await p.close()


async def test_bootstraps_a_clean_database_through_migration_pool_then_pool(
    scratch_db, tmp_path
):
    """The regression the fix-round review caught: opening any connection through
    pool() on a database where the vector extension does not exist raises inside
    register_vector's init callback -- and every real database is in exactly that
    state before its first migration runs. This composes the real startup order end
    to end, with no admin connection creating anything out of band: migration_pool()
    opens connections with no codec, run_migrations() applies the file that creates
    the extension, and only then does pool() open its first connection."""
    (tmp_path / "0001_vector.sql").write_text(
        "CREATE EXTENSION IF NOT EXISTS vector;"
        " CREATE TABLE embeddings (id integer PRIMARY KEY, v vector(3));"
    )

    mpool = await migration_pool(dsn=_scratch_dsn(scratch_db))
    try:
        applied = await run_migrations(mpool, tmp_path)
    finally:
        await mpool.close()
    assert applied == ["0001_vector.sql"]

    apool = await pool(dsn=_scratch_dsn(scratch_db))
    try:
        await probe(apool)
        async with apool.acquire() as conn:
            # Values exactly representable in float32 (vector's storage type), so the
            # round-trip assertion below cannot fail on precision alone.
            await conn.execute(
                "INSERT INTO embeddings (id, v) VALUES ($1, $2)", 1, [0.5, 1.0, 1.5]
            )
            row = await conn.fetchrow("SELECT v FROM embeddings WHERE id = 1")
        assert row["v"].to_list() == [0.5, 1.0, 1.5]
    finally:
        await apool.close()
