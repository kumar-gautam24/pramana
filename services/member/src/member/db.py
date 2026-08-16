"""Async engine and session factory.

`create_async_engine` opens no connection, so constructing it proves nothing about the
URL beyond it being parseable -- a database that does not exist surfaces only on the
first query. Startup probes the engine (see the lifespan in main.py) so misconfiguration
fails before the service accepts traffic."""

import hashlib
from pathlib import Path

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from member.config import get_settings

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# --- ADR-0013: the asyncpg foundation, added alongside the engine above --------------
#
# This sweep migrates queries off the engine above one task at a time; until the last
# one lands, both have to work. Nothing above this line is touched by this change.
#
# This file intentionally duplicates services/policy/src/policy/db.py rather than
# sharing it from packages/common: a migration runner is infrastructure, not a wire
# contract, and packages/common is reserved for the latter. Two small files that can
# diverge (member never needs the vector codec policy does) beat a shared dependency
# that would couple the two services' deploy cycles for no gain.


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


async def run_migrations(pool: asyncpg.Pool, directory: Path) -> list[str]:
    """Apply every migrations/*.sql file not yet recorded in schema_migrations, in
    filename order, each inside its own transaction with its version recorded in that
    same transaction -- so a file that fails leaves neither a partial schema change nor
    a row claiming it succeeded.

    Refuses outright, before applying anything, if recorded history and disk disagree:
    a recorded version with no file on disk, or an applied file whose bytes no longer
    match its recorded checksum. Both mean someone edited history after the fact, and
    applying further migrations on top of an unknown starting point would turn that
    into silent drift instead of a loud failure -- the one thing ADR-0013 calls
    load-bearing about this runner.
    """
    files = sorted(directory.glob("*.sql"))
    on_disk = {file.name for file in files}

    async with pool.acquire() as connection:
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
            for row in await connection.fetch("SELECT version, checksum FROM schema_migrations")
        }

        missing = sorted(set(recorded) - on_disk)
        if missing:
            raise MigrationError(
                f"recorded migration(s) have no file on disk: {', '.join(missing)}"
            )

        applied: list[str] = []
        for file in files:
            checksum = hashlib.sha256(file.read_bytes()).hexdigest()

            if file.name in recorded:
                if recorded[file.name] != checksum:
                    raise MigrationError(
                        f"{file.name} has changed since it was applied "
                        f"(recorded checksum {recorded[file.name]}, now {checksum})"
                    )
                continue

            async with connection.transaction():
                await connection.execute(file.read_text())
                await connection.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                    file.name,
                    checksum,
                )
            applied.append(file.name)

    return applied
