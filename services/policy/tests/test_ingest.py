import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from policy.cms import parse_ncd_response
from policy.ingest import ingest_ncd
from policy.models import Chunk, Policy

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ncd-226.json").read_text())


class StubEmbedder:
    """Deterministic and instant. The embedder is exercised for real in Task 8; here it
    would only make the test slow and flaky."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7)] * 384 for t in texts]


@pytest.fixture
async def session(db_session):
    return db_session


async def test_ingest_stores_the_policy_and_its_chunks(session):
    result = await ingest_ncd(session, StubEmbedder(), parse_ncd_response(FIXTURE))

    assert result.policies_added == 1
    assert result.chunks_added > 0

    policy = (await session.execute(select(Policy))).scalar_one()
    assert policy.display_id == "240.4"
    assert policy.effective_from == date(2008, 3, 13)
    assert policy.effective_to is None


async def test_ingest_is_idempotent(session):
    records = parse_ncd_response(FIXTURE)
    first = await ingest_ncd(session, StubEmbedder(), records)
    second = await ingest_ncd(session, StubEmbedder(), records)

    assert second.policies_added == 0
    assert second.skipped == 1
    assert second.chunks_added == 0

    chunk_count = (await session.execute(select(func.count()).select_from(Chunk))).scalar_one()
    assert chunk_count == first.chunks_added


async def test_every_chunk_carries_a_heading_path(session):
    await ingest_ncd(session, StubEmbedder(), parse_ncd_response(FIXTURE))

    chunks = (await session.execute(select(Chunk))).scalars().all()
    assert chunks
    assert all(c.heading_path.strip() for c in chunks)


async def test_chunks_are_removed_with_their_policy(session):
    """Chunks outliving their policy would be retrievable and uncitable."""
    await ingest_ncd(session, StubEmbedder(), parse_ncd_response(FIXTURE))
    policy = (await session.execute(select(Policy))).scalar_one()

    await session.delete(policy)
    await session.flush()

    remaining = (await session.execute(select(func.count()).select_from(Chunk))).scalar_one()
    assert remaining == 0
