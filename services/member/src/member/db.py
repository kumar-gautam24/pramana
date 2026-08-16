"""The asyncpg foundation: pool(), probe(), run_migrations(). Per ADR-0013.

The SQLAlchemy engine/`SessionFactory` below are dead code as of this task -- main.py's
lifespan now probes through `pool()`/`probe()`, and no route or repository imports
either name any more. They are left in place, unused, because removing them is Task 6's
job (the same sweep that drops the `sqlalchemy`/`alembic` dependencies from
pyproject.toml); deleting them here would be a scope creep this task didn't need.

This file intentionally duplicates services/policy/src/policy/db.py rather than
sharing it from packages/common: a migration runner is infrastructure, not a wire
contract, and packages/common is reserved for the latter. Two small files that can
diverge (member never needs the vector codec policy does) beat a shared dependency
that would couple the two services' deploy cycles for no gain."""

import hashlib
from pathlib import Path

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from member.config import get_settings

# Unused (see module docstring) -- kept only because removing it is Task 6's job.
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class MigrationError(Exception):
    """Recorded migration history and the migrations/ directory disagree. Raised
    instead of guessing which one is right, because guessing is exactly how
    environments end up silently divergent -- see ADR-0013."""


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    # Settings holds the SQLAlchemy-style URL because the engine above still needs it;
    # asyncpg's own DSN parser only accepts the "postgresql://" scheme.
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def pool(dsn: str | None = None) -> asyncpg.Pool:
    """Created once at startup and held for the process lifetime. `dsn` overrides the
    configured URL for tests that need a scratch database; the running service always
    calls pool() with no arguments.

    min_size=0 is deliberate, matching the comment above: asyncpg's default eagerly
    opens min_size connections during create_pool(), which would make a bad
    DATABASE_URL fail here instead of at probe() -- fine in itself, but it would mean
    two different places, the pool and the probe, race to be the one that catches it.
    Keeping pool() connection-free makes probe() the single, deliberate startup check.
    """
    return await asyncpg.create_pool(
        dsn or _asyncpg_dsn(get_settings().database_url),
        min_size=0,
    )


async def probe(pool: asyncpg.Pool) -> None:
    """The cheapest query that proves the pool reaches a database that answers. Raises
    whatever the driver raises, so a bad DATABASE_URL fails at startup with the real
    cause instead of on the first request."""
    async with pool.acquire() as connection:
        await connection.execute("SELECT 1")


# Arbitrary, fixed key for the session-level advisory lock run_migrations() takes.
# Advisory locks are scoped per-database, and policy and member never share one, so
# this literal being reused verbatim in policy/db.py cannot cause a cross-service
# collision. Any fixed value works; this one has no meaning beyond being constant.
_MIGRATION_LOCK_KEY = 847_216_305


async def run_migrations(pool: asyncpg.Pool, directory: Path) -> list[str]:
    """Apply every migrations/*.sql file not yet recorded in schema_migrations, in
    filename order, each inside its own transaction with its version recorded in that
    same transaction -- so a file that fails leaves neither a partial schema change nor
    a row claiming it succeeded.

    Refuses outright, before applying anything, if recorded history and disk disagree:
    a recorded version with no file on disk, or an applied file whose bytes no longer
    match its recorded checksum. Both are checked in one pre-flight pass, before the
    first unapplied file runs -- catching a mismatch mid-run would mean this call had
    already advanced the schema on top of history it had just proven corrupt. Both
    conditions mean someone edited history after the fact, and continuing on an unknown
    starting point would turn that into silent drift instead of a loud failure -- the
    one thing ADR-0013 calls load-bearing about this runner.

    Takes a session-level pg_advisory_lock for the duration of the run, so two
    replicas starting together serialize instead of racing: the second blocks here and,
    once the first releases the lock, finds every file already recorded and returns an
    empty list. Without it, two connections can both pass CREATE TABLE IF NOT EXISTS's
    existence check before either commits (a known Postgres race on the catalog) or
    both attempt to record the same version, and the failure surfaces as a
    UniqueViolationError that reads like corruption instead of ordinary concurrency.
    A session lock, not pg_advisory_xact_lock, because the run's several per-file
    transactions each commit individually -- a lock tied to any single one of them
    would release before the next file's transaction begins.
    """
    files = sorted(directory.glob("*.sql"))
    on_disk = {file.name for file in files}

    async with pool.acquire() as connection:
        await connection.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_KEY)
        try:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now(),
                    checksum text NOT NULL
                )
                """
            )

            recorded = {
                row["version"]: row["checksum"]
                for row in await connection.fetch(
                    "SELECT version, checksum FROM schema_migrations"
                )
            }

            missing = sorted(set(recorded) - on_disk)
            if missing:
                raise MigrationError(
                    f"recorded migration(s) have no file on disk: {', '.join(missing)}"
                )

            # Every file's checksum, computed once and reused below so an unapplied
            # file is not re-read from disk a second time in the apply loop.
            checksums = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in files}

            mismatched = sorted(
                name for name, checksum in recorded.items() if checksums[name] != checksum
            )
            if mismatched:
                raise MigrationError(
                    "applied migration(s) no longer match their recorded checksum: "
                    + ", ".join(mismatched)
                )

            applied: list[str] = []
            for file in files:
                if file.name in recorded:
                    continue

                async with connection.transaction():
                    await connection.execute(file.read_text())
                    await connection.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                        file.name,
                        checksums[file.name],
                    )
                applied.append(file.name)
        finally:
            await connection.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_KEY)

    return applied
