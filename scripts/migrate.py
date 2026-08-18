"""Applies a service's pending migrations.

Migrations run out-of-band rather than from a service's lifespan, deliberately: a
replica that migrates on boot turns a schema change into something that happens
whenever a container restarts, at whatever moment an orchestrator chooses. Running
them as a step an operator takes keeps the change where it can be observed and
rolled back.

The runner itself is `run_migrations()` in each service's `db.py`. It is transactional
per file, records each version with the file's checksum, refuses to run when an applied
file has changed or gone missing, and takes an advisory lock so two replicas racing
apply nothing twice. This script is just the way to reach it from a terminal.

Usage (dsn is asyncpg-style, i.e. `postgresql://`, not `postgresql+asyncpg://`):

    cd services/policy && uv run python ../../scripts/migrate.py \\
        postgresql://pramana:pramana@localhost:5432/pramana_policy \\
        migrations
"""

import asyncio
import importlib
import sys
from pathlib import Path


def _service_db_module():
    """Imported from whichever service's directory this is run in, so the script stays
    a thin entry point rather than a third copy of the runner."""
    failures = {}
    for name in ("policy", "member"):
        try:
            return importlib.import_module(f"{name}.db")
        except ModuleNotFoundError as error:
            # Distinguish "this is not that service's directory" from "that service is
            # here but one of its own imports is broken". Swallowing the second reports
            # the wrong problem entirely, and it is the one that actually needs fixing.
            if error.name not in (name, f"{name}.db"):
                raise
            failures[name] = str(error)
    print(
        "no service package importable -- run this from inside services/<name>. "
        f"Tried: {failures}",
        file=sys.stderr,
    )
    raise SystemExit(2)


async def migrate(dsn: str, migrations_dir: Path) -> list[str]:
    db = _service_db_module()
    # migration_pool where the service has one: policy's application pool registers the
    # pgvector codec on connect, which fails on a database that has not yet run the
    # migration creating the extension.
    opener = getattr(db, "migration_pool", db.pool)
    pool = await opener(dsn=dsn)
    try:
        return await db.run_migrations(pool, migrations_dir)
    finally:
        await pool.close()


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: migrate.py <dsn> <migrations_dir>", file=sys.stderr)
        raise SystemExit(2)

    applied = asyncio.run(migrate(sys.argv[1], Path(sys.argv[2])))
    print(f"applied: {', '.join(applied)}" if applied else "nothing to apply (up to date)")


if __name__ == "__main__":
    main()
