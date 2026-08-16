from policy.models import Chunk, Policy


def test_policy_is_unique_per_document_version():
    """Ingest must be idempotent: re-running it for a version already stored is a
    no-op rather than a duplicate corpus."""
    constraints = {c.name for c in Policy.__table__.constraints if c.name}

    assert "uq_policies_document_id_version" in constraints


def test_chunk_embedding_is_384_dimensions():
    """bge-small-en-v1.5 emits 384 dimensions. A mismatch here fails at insert time with
    an opaque pgvector error, so pin it in the model."""
    assert Chunk.__table__.c.embedding.type.dim == 384


def test_chunk_keeps_its_heading_path():
    """A citation that cannot name the section it came from is not auditable, so the
    column is not nullable."""
    assert Chunk.__table__.c.heading_path.nullable is False
