"""Records migrations/*.sql files as already applied, without running their DDL, for a
database that reached the same schema by another route -- here, both `pramana_member`
and `pramana_policy` predate ADR-0013 and were built by Alembic. `run_migrations()`
(services/*/src/*/db.py) has no way to know that; pointed at such a database it finds
no `schema_migrations` row, tries to run `0001_*.sql` for real, and dies on
`relation "..." already exists`. This script is the reproducible fix for that, in place
of a report nobody will read.

How it decides the schema is genuinely already there, rather than trusting the caller's
say-so: each not-yet-recorded file is executed inside a transaction that is *always*
rolled back, real run or not.

  * If that raises -- a bare `CREATE TABLE`/`CREATE INDEX` clashing with an object that
    already exists -- the clash itself proves the schema is present, and only then does
    the file get recorded as applied, in a second, real transaction.
  * If it does not raise, the file just applied cleanly against a database that did not
    have this schema yet. That means it genuinely needs applying -- so this refuses
    outright rather than silently adopt-and-skip it. Run the real migration runner
    instead.

This relies on every migration file containing at least one statement that is not
already idempotent (a bare `CREATE TABLE`, not `CREATE TABLE IF NOT EXISTS`) -- true of
both `0001_policies_and_chunks.sql` and `0001_member_records.sql` today. A file made
entirely of idempotent statements would defeat the "clash proves presence" check; no
migration in either service is like that yet, but a future one that is would need a
different adoption path, not this script.

Usage (dsn is asyncpg-style, i.e. `postgresql://`, not `postgresql+asyncpg://`):

    cd services/member && uv run python ../../scripts/adopt_migrations.py \\
        postgresql://pramana:pramana@localhost:5432/pramana_member \\
        migrations
"""

import asyncio
import hashlib
import sys
from pathlib import Path

import asyncpg


async def adopt(dsn: str, migrations_dir: Path) -> list[str]:
    files = sorted(migrations_dir.glob("*.sql"))

    conn = await asyncpg.connect(dsn)
    try:
        # Same shape run_migrations() creates on its own first run -- adopting a
        # database must leave it able to fall through to the real runner afterward.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now(),
                checksum text NOT NULL
            )
            """
        )
        recorded = {
            row["version"] for row in await conn.fetch("SELECT version FROM schema_migrations")
        }

        adopted: list[str] = []
        for file in files:
            if file.name in recorded:
                continue

            already_present = False
            transaction = conn.transaction()
            await transaction.start()
            try:
                await conn.execute(file.read_text())
            except asyncpg.PostgresError:
                already_present = True
            finally:
                # Unconditional: this run must never be the one that actually applies
                # the file's DDL for real, on either branch.
                await transaction.rollback()

            if not already_present:
                raise RuntimeError(
                    f"{file.name} applied cleanly against this database -- its schema "
                    "was not already present, so it genuinely needs applying. Run the "
                    "real migration runner (run_migrations) instead of this script."
                )

            checksum = hashlib.sha256(file.read_bytes()).hexdigest()
            await conn.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)",
                file.name,
                checksum,
            )
            adopted.append(file.name)
    finally:
        await conn.close()

    return adopted


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: adopt_migrations.py <dsn> <migrations_dir>", file=sys.stderr)
        raise SystemExit(2)

    dsn, migrations_dir = sys.argv[1], Path(sys.argv[2])
    adopted = asyncio.run(adopt(dsn, migrations_dir))
    print(f"adopted: {', '.join(adopted)}" if adopted else "nothing to adopt (already recorded)")


if __name__ == "__main__":
    main()
