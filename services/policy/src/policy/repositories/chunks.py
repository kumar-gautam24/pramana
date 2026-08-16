"""Raw SQL for the `chunks` table: dense and lexical candidate search, the fetch that
feeds reranking, and the ingest insert.

Dense search understands meaning; lexical search catches exact tokens like "AHI" and
"Type IV" that embeddings blur -- see policy.retrieval for how the two are fused."""

import asyncpg

from policy.models import Chunk, Policy

_CHUNK_COLUMNS = "id, policy_id, ordinal, heading_path, text, embedding"


def _row_to_chunk(row: asyncpg.Record) -> Chunk:
    return Chunk(
        id=row["id"],
        policy_id=row["policy_id"],
        ordinal=row["ordinal"],
        heading_path=row["heading_path"],
        text=row["text"],
        # The registered vector codec (policy.db.pool) hands back a pgvector.Vector,
        # not a plain list -- .to_list() is its documented conversion.
        embedding=row["embedding"].to_list(),
    )


async def dense_search(
    conn, vector: list[float], limit: int, policy_ids: list[int] | None
) -> list[int]:
    """Chunk ids nearest `vector` by cosine distance, nearest first."""
    if policy_ids is not None:
        rows = await conn.fetch(
            """
            SELECT id FROM chunks
            WHERE policy_id = ANY($1::int[])
            ORDER BY embedding <=> $2
            LIMIT $3
            """,
            policy_ids,
            vector,
            limit,
        )
    else:
        rows = await conn.fetch(
            "SELECT id FROM chunks ORDER BY embedding <=> $1 LIMIT $2",
            vector,
            limit,
        )
    return [row["id"] for row in rows]


async def lexical_search(
    conn, query: str, limit: int, policy_ids: list[int] | None
) -> list[int]:
    """Chunk ids whose text matches `query`'s tokens, ranked by ts_rank descending."""
    if policy_ids is not None:
        rows = await conn.fetch(
            """
            SELECT id FROM chunks
            WHERE tsv @@ plainto_tsquery('english', $1) AND policy_id = ANY($2::int[])
            ORDER BY ts_rank(tsv, plainto_tsquery('english', $1)) DESC
            LIMIT $3
            """,
            query,
            policy_ids,
            limit,
        )
    else:
        rows = await conn.fetch(
            """
            SELECT id FROM chunks
            WHERE tsv @@ plainto_tsquery('english', $1)
            ORDER BY ts_rank(tsv, plainto_tsquery('english', $1)) DESC
            LIMIT $2
            """,
            query,
            limit,
        )
    return [row["id"] for row in rows]


async def fetch_with_policies(conn, chunk_ids: list[int]) -> list[tuple[Chunk, Policy]]:
    """The chunk and its policy for each id, ordered by chunks.id -- so tied rerank
    scores land on the same winner every run instead of inheriting whatever order
    Postgres happens to return an IN-list scan in, which is unspecified."""
    rows = await conn.fetch(
        """
        SELECT
            chunks.id AS chunk_id,
            chunks.policy_id AS chunk_policy_id,
            chunks.ordinal AS chunk_ordinal,
            chunks.heading_path AS chunk_heading_path,
            chunks.text AS chunk_text,
            chunks.embedding AS chunk_embedding,
            policies.id AS policy_id,
            policies.document_id AS policy_document_id,
            policies.document_version AS policy_document_version,
            policies.display_id AS policy_display_id,
            policies.title AS policy_title,
            policies.effective_from AS policy_effective_from,
            policies.effective_to AS policy_effective_to,
            policies.benefit_category AS policy_benefit_category,
            policies.source_url AS policy_source_url,
            policies.ingested_at AS policy_ingested_at
        FROM chunks
        JOIN policies ON chunks.policy_id = policies.id
        WHERE chunks.id = ANY($1::int[])
        ORDER BY chunks.id
        """,
        chunk_ids,
    )
    return [
        (
            Chunk(
                id=row["chunk_id"],
                policy_id=row["chunk_policy_id"],
                ordinal=row["chunk_ordinal"],
                heading_path=row["chunk_heading_path"],
                text=row["chunk_text"],
                embedding=row["chunk_embedding"].to_list(),
            ),
            Policy(
                id=row["policy_id"],
                document_id=row["policy_document_id"],
                document_version=row["policy_document_version"],
                display_id=row["policy_display_id"],
                title=row["policy_title"],
                effective_from=row["policy_effective_from"],
                effective_to=row["policy_effective_to"],
                benefit_category=row["policy_benefit_category"],
                source_url=row["policy_source_url"],
                ingested_at=row["policy_ingested_at"],
            ),
        )
        for row in rows
    ]


async def insert_many(
    conn, policy_id: int, rows: list[tuple[int, str, str, list[float]]]
) -> list[Chunk]:
    """rows: (ordinal, heading_path, text, embedding) already paired with an embedding
    by the caller -- this layer only knows how to write a chunk row, not how chunking
    or embedding produced one.

    One INSERT per row rather than a single executemany(): executemany() has no
    RETURNING equivalent in asyncpg's batch protocol, and both ingest (which needs a
    count) and tests (which need the database-assigned ids) want the inserted rows
    back. The corpus is small enough that this costs nothing that matters."""
    inserted = []
    for ordinal, heading_path, text, embedding in rows:
        row = await conn.fetchrow(
            f"""
            INSERT INTO chunks (policy_id, ordinal, heading_path, text, embedding)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING {_CHUNK_COLUMNS}
            """,
            policy_id,
            ordinal,
            heading_path,
            text,
            embedding,
        )
        inserted.append(_row_to_chunk(row))
    return inserted


async def count(conn) -> int:
    return await conn.fetchval("SELECT count(*) FROM chunks")


async def fetch_all(conn) -> list[Chunk]:
    rows = await conn.fetch(f"SELECT {_CHUNK_COLUMNS} FROM chunks")
    return [_row_to_chunk(row) for row in rows]
