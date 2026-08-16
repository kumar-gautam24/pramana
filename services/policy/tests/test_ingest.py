import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from policy.cms import NcdRecord, parse_ncd_response
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


async def test_a_record_with_no_prose_is_skipped_not_stored(session):
    """A policy with no chunks can never be cited. Storing it anyway would still satisfy
    the (document_id, document_version) uniqueness check on every later run, so the skip
    would become permanent -- content CMS attaches to that version afterward would be
    silently and forever shadowed by the empty row ingested first."""
    empty = NcdRecord(
        document_id="999",
        document_version=1,
        display_id="999.9",
        title="Empty Record",
        effective_from=date(2020, 1, 1),
        effective_to=None,
        benefit_category="",
        sections_html={},
        source_url="https://example.invalid/999",
    )

    result = await ingest_ncd(session, StubEmbedder(), [empty])

    assert result.policies_added == 0
    assert result.chunks_added == 0
    assert result.skipped == 1
    assert (await session.execute(select(Policy))).scalar_one_or_none() is None

    # The same document/version, now carrying real content, must still be ingestible --
    # the earlier skip must not have left behind a row that permanently blocks it.
    filled = NcdRecord(
        document_id="999",
        document_version=1,
        display_id="999.9",
        title="Empty Record",
        effective_from=date(2020, 1, 1),
        effective_to=None,
        benefit_category="",
        sections_html={"other_text": "<p>Some prose worth retrieving.</p>"},
        source_url="https://example.invalid/999",
    )

    second = await ingest_ncd(session, StubEmbedder(), [filled])

    assert second.policies_added == 1
    assert second.chunks_added > 0
    assert (await session.execute(select(Policy))).scalar_one().document_id == "999"
