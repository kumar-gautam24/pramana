"""The migration runner is the one piece ADR-0013 calls load-bearing: it must apply
files in order, never twice, and refuse to run rather than let recorded history and
disk silently disagree. Every test here runs against a scratch database created and
dropped by the test itself -- never against pramana_policy or its _test sibling."""

import asyncio
import uuid

import asyncpg
import pytest

from policy.db import MigrationError, run_migrations

ADMIN_DSN = "postgresql://pramana:pramana@localhost:5432/postgres"


def _scratch_dsn(name: str) -> str:
    return f"postgresql://pramana:pramana@localhost:5432/{name}"


@pytest.fixture
async def scratch_db():
    """A database this test owns end to end, so a bug in the runner can never corrupt
    pramana_policy_test or leave a scratch database behind for the next run."""
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
            # FORCE drops even if the pool under test still holds a connection open --
            # a leaked connection must never be the reason a scratch database survives.
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        finally:
            await admin.close()


@pytest.fixture
async def scratch_pool(scratch_db):
    pool = await asyncpg.create_pool(_scratch_dsn(scratch_db))
    try:
        yield pool
    finally:
        await pool.close()


def _write(directory, filename, sql):
    (directory / filename).write_text(sql)


async def _applied_versions(pool):
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
    return [r["version"] for r in rows]


async def _table_exists(pool, table_name):
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table_name}")


async def test_applies_two_migrations_in_order(scratch_pool, tmp_path):
    _write(tmp_path, "0001_create_foo.sql", "CREATE TABLE foo (id integer PRIMARY KEY);")
    _write(tmp_path, "0002_add_bar.sql", "ALTER TABLE foo ADD COLUMN bar text;")

    applied = await run_migrations(scratch_pool, tmp_path)

    assert applied == ["0001_create_foo.sql", "0002_add_bar.sql"]
    assert await _applied_versions(scratch_pool) == ["0001_create_foo.sql", "0002_add_bar.sql"]
    assert await _table_exists(scratch_pool, "foo")


async def test_rerun_applies_nothing(scratch_pool, tmp_path):
    _write(tmp_path, "0001_create_foo.sql", "CREATE TABLE foo (id integer PRIMARY KEY);")

    first = await run_migrations(scratch_pool, tmp_path)
    second = await run_migrations(scratch_pool, tmp_path)

    assert first == ["0001_create_foo.sql"]
    assert second == []
    assert await _applied_versions(scratch_pool) == ["0001_create_foo.sql"]


async def test_failing_migration_leaves_no_partial_state(scratch_pool, tmp_path):
    _write(tmp_path, "0001_create_foo.sql", "CREATE TABLE foo (id integer PRIMARY KEY);")
    # Two statements in one file: the first would succeed alone, the second never will.
    # If the transaction boundary is wrong, "leftover" survives and this test catches it.
    _write(
        tmp_path,
        "0002_bad.sql",
        "CREATE TABLE leftover (id integer); SELECT this_function_does_not_exist();",
    )

    with pytest.raises(asyncpg.PostgresError):
        await run_migrations(scratch_pool, tmp_path)

    assert await _applied_versions(scratch_pool) == ["0001_create_foo.sql"]
    assert not await _table_exists(scratch_pool, "leftover")


async def test_missing_recorded_file_raises(scratch_pool, tmp_path):
    _write(tmp_path, "0001_create_foo.sql", "CREATE TABLE foo (id integer PRIMARY KEY);")
    await run_migrations(scratch_pool, tmp_path)

    (tmp_path / "0001_create_foo.sql").unlink()

    with pytest.raises(MigrationError):
        await run_migrations(scratch_pool, tmp_path)


async def test_changed_applied_file_raises(scratch_pool, tmp_path):
    _write(tmp_path, "0001_create_foo.sql", "CREATE TABLE foo (id integer PRIMARY KEY);")
    await run_migrations(scratch_pool, tmp_path)

    # Editing an already-applied file after the fact is exactly the "someone edited
    # history" case ADR-0013 calls out -- the checksum is what catches it.
    _write(
        tmp_path,
        "0001_create_foo.sql",
        "CREATE TABLE foo (id integer PRIMARY KEY, extra text);",
    )

    with pytest.raises(MigrationError):
        await run_migrations(scratch_pool, tmp_path)


async def test_creates_schema_migrations_table_if_absent(scratch_pool, tmp_path):
    assert not await _table_exists(scratch_pool, "schema_migrations")
    await run_migrations(scratch_pool, tmp_path)
    assert await _table_exists(scratch_pool, "schema_migrations")


async def test_checksum_mismatch_is_caught_before_applying_new_files(scratch_pool, tmp_path):
    """Fix-round regression: the checksum check used to sit inside the per-file apply
    loop, so a corrupted file that sorted *after* a brand-new, not-yet-applied one let
    the new one apply before the mismatch was ever noticed. 0003 here is the corrupted
    file; 0002 is the new one that must never be touched once 0003 fails to verify."""
    _write(tmp_path, "0001_create_foo.sql", "CREATE TABLE foo (id integer PRIMARY KEY);")
    _write(tmp_path, "0003_create_baz.sql", "CREATE TABLE baz (id integer PRIMARY KEY);")
    await run_migrations(scratch_pool, tmp_path)

    _write(
        tmp_path,
        "0003_create_baz.sql",
        "CREATE TABLE baz (id integer PRIMARY KEY, extra text);",
    )
    _write(tmp_path, "0002_create_bar.sql", "CREATE TABLE bar (id integer PRIMARY KEY);")

    with pytest.raises(MigrationError):
        await run_migrations(scratch_pool, tmp_path)

    assert not await _table_exists(scratch_pool, "bar")
    assert await _applied_versions(scratch_pool) == [
        "0001_create_foo.sql",
        "0003_create_baz.sql",
    ]


async def test_ddl_rolls_back_when_recording_the_version_fails(scratch_pool, tmp_path):
    """Mutation-tested boundary: the file's SQL and the schema_migrations insert are two
    separate execute() calls, joined only by the explicit connection.transaction().
    Postgres's own implicit transaction around a multi-statement simple query does not
    reach across that boundary, so this is the one thing the explicit transaction is
    actually responsible for. The CHECK constraint makes the insert fail regardless of
    file content -- a real sha256 hex digest is always 64 characters -- so this does not
    depend on racing anything."""
    async with scratch_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now(),
                checksum text NOT NULL CHECK (length(checksum) <= 8)
            )
            """
        )
    _write(tmp_path, "0001_create_ddl_survivor.sql", "CREATE TABLE ddl_survivor (id integer);")

    with pytest.raises(asyncpg.PostgresError):
        await run_migrations(scratch_pool, tmp_path)

    assert not await _table_exists(scratch_pool, "ddl_survivor")
    assert await _applied_versions(scratch_pool) == []


async def test_two_concurrent_runners_do_not_double_apply(scratch_db, tmp_path):
    """Two replicas starting together must serialize on the advisory lock rather than
    race: whichever acquires it first applies everything, and the other -- unblocked
    only after the first commits and releases -- finds nothing left to do. Neither may
    raise, and nothing may be applied twice."""
    _write(tmp_path, "0001_create_foo.sql", "CREATE TABLE foo (id integer PRIMARY KEY);")
    _write(tmp_path, "0002_create_bar.sql", "CREATE TABLE bar (id integer PRIMARY KEY);")

    pool_a = await asyncpg.create_pool(_scratch_dsn(scratch_db))
    pool_b = await asyncpg.create_pool(_scratch_dsn(scratch_db))
    try:
        first, second = await asyncio.gather(
            run_migrations(pool_a, tmp_path),
            run_migrations(pool_b, tmp_path),
        )
    finally:
        await pool_a.close()
        await pool_b.close()

    assert sorted(first + second) == ["0001_create_foo.sql", "0002_create_bar.sql"]
    assert {len(first), len(second)} == {0, 2}

    verify_pool = await asyncpg.create_pool(_scratch_dsn(scratch_db))
    try:
        assert await _applied_versions(verify_pool) == [
            "0001_create_foo.sql",
            "0002_create_bar.sql",
        ]
    finally:
        await verify_pool.close()
