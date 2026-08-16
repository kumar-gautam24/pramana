"""pool() and probe() are the startup-time checks ADR-0013 requires to survive the
switch away from SQLAlchemy: a bad DATABASE_URL must fail before the service accepts
traffic, and policy's vector column must round-trip through asyncpg without a
hand-rolled codec."""

import uuid

import asyncpg
import pytest

from policy.db import pool, probe

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
    # pool()'s init callback registers the vector codec on every connection it opens,
    # so even a probe-only test needs the extension present, same as a real database.
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
