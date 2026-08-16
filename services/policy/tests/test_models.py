"""Schema guarantees the models module used to state as ORM metadata
(Policy.__table__.constraints, Chunk.__table__.c...type.dim, ...nullable). Frozen
dataclasses carry no such metadata, so these are rewritten as behavioural checks
against the real schema in migrations/0001_policies_and_chunks.sql -- each one is
strictly stronger than the assertion it replaces, since it exercises the constraint
instead of reading its declaration."""

from datetime import date

import asyncpg
import pytest

from policy.repositories import chunks as chunks_repo
from policy.repositories import policies as policies_repo


async def test_policy_is_unique_per_document_version(db_session):
    """Ingest must be idempotent: re-running it for a version already stored is a
    no-op rather than a duplicate corpus -- uq_policies_document_id_version is what
    makes that a database guarantee rather than a convention application code could
    violate."""
    kwargs = dict(
        document_id="800",
        document_version=1,
        display_id="800.1",
        title="Test Policy",
        effective_from=date(2020, 1, 1),
        effective_to=None,
        benefit_category="",
        source_url="https://example.invalid/800",
    )
    await policies_repo.insert(db_session, **kwargs)

    with pytest.raises(asyncpg.UniqueViolationError):
        await policies_repo.insert(db_session, **kwargs)


async def test_chunk_embedding_must_be_384_dimensions(db_session):
    """bge-small-en-v1.5 emits 384 dimensions. A mismatch here fails at insert time with
    a pgvector error, not silent truncation or padding."""
    policy = await policies_repo.insert(
        db_session,
        document_id="801",
        document_version=1,
        display_id="801.1",
        title="Test Policy",
        effective_from=date(2020, 1, 1),
        effective_to=None,
        benefit_category="",
        source_url="https://example.invalid/801",
    )

    with pytest.raises(asyncpg.PostgresError):
        await chunks_repo.insert_many(
            db_session, policy.id, [(0, "Root", "text", [0.0] * 10)]
        )


async def test_chunk_heading_path_cannot_be_null(db_session):
    """A citation that cannot name the section it came from is not auditable, so the
    column is NOT NULL -- enforced by the schema, not just documented on the model."""
    policy = await policies_repo.insert(
        db_session,
        document_id="802",
        document_version=1,
        display_id="802.1",
        title="Test Policy",
        effective_from=date(2020, 1, 1),
        effective_to=None,
        benefit_category="",
        source_url="https://example.invalid/802",
    )

    with pytest.raises(asyncpg.NotNullViolationError):
        await chunks_repo.insert_many(
            db_session, policy.id, [(0, None, "text", [0.0] * 384)]
        )
