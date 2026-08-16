"""Raw SQL for the `policies` table.

Every statement here is one a reviewer can check against the policy it implements --
see docs/decisions/0013."""

from datetime import date

import asyncpg

from policy.models import Policy

_COLUMNS = (
    "id, document_id, document_version, display_id, title, effective_from, "
    "effective_to, benefit_category, source_url, ingested_at"
)


def _row_to_policy(row: asyncpg.Record) -> Policy:
    return Policy(
        id=row["id"],
        document_id=row["document_id"],
        document_version=row["document_version"],
        display_id=row["display_id"],
        title=row["title"],
        effective_from=row["effective_from"],
        effective_to=row["effective_to"],
        benefit_category=row["benefit_category"],
        source_url=row["source_url"],
        ingested_at=row["ingested_at"],
    )


async def fetch_all(conn) -> list[Policy]:
    """Every version of every policy. The governing-version lookup in retrieval.py
    resolves from this full table, before retrieval narrows anything -- never from a
    set of already-retrieved candidates. See policy.retrieval._governing_policy_ids."""
    rows = await conn.fetch(f"SELECT {_COLUMNS} FROM policies")
    return [_row_to_policy(row) for row in rows]


async def find_by_document_version(
    conn, document_id: str, document_version: int
) -> Policy | None:
    """Backs ingest's idempotency check: a version already stored here means this
    ingest call is a no-op, per the uq_policies_document_id_version constraint."""
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM policies WHERE document_id = $1 AND document_version = $2",
        document_id,
        document_version,
    )
    return _row_to_policy(row) if row is not None else None


async def insert(
    conn,
    *,
    document_id: str,
    document_version: int,
    display_id: str,
    title: str,
    effective_from: date,
    effective_to: date | None,
    benefit_category: str,
    source_url: str,
) -> Policy:
    row = await conn.fetchrow(
        f"""
        INSERT INTO policies (
            document_id, document_version, display_id, title,
            effective_from, effective_to, benefit_category, source_url
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING {_COLUMNS}
        """,
        document_id,
        document_version,
        display_id,
        title,
        effective_from,
        effective_to,
        benefit_category,
        source_url,
    )
    return _row_to_policy(row)
