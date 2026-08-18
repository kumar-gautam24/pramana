"""pool() and probe() are the startup-time checks ADR-0013 requires to survive the
switch away from SQLAlchemy: a bad DATABASE_URL must fail before the service accepts
traffic, the invariant that survived the switch away from SQLAlchemy unchanged."""

import uuid

import asyncpg
import pytest

from member.db import pool, probe

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
