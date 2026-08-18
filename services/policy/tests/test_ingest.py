import json
from datetime import date
from pathlib import Path

import pytest

from policy.repositories import chunks as chunks_repo
from policy.repositories import policies as policies_repo
from policy.services.cms import NcdRecord, parse_ncd_response
from policy.services.ingest import ingest_ncd

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

    policies = await policies_repo.fetch_all(session)
    assert len(policies) == 1
    assert policies[0].display_id == "240.4"
    assert policies[0].effective_from == date(2008, 3, 13)
    assert policies[0].effective_to is None


async def test_ingest_is_idempotent(session):
    records = parse_ncd_response(FIXTURE)
    first = await ingest_ncd(session, StubEmbedder(), records)
    second = await ingest_ncd(session, StubEmbedder(), records)

    assert second.policies_added == 0
    assert second.skipped == 1
    assert second.chunks_added == 0

    assert await chunks_repo.count(session) == first.chunks_added


async def test_every_chunk_carries_a_heading_path(session):
    await ingest_ncd(session, StubEmbedder(), parse_ncd_response(FIXTURE))

    chunks = await chunks_repo.fetch_all(session)
    assert chunks
    assert all(c.heading_path.strip() for c in chunks)


async def test_chunks_are_removed_with_their_policy(session):
    """Chunks outliving their policy would be retrievable and uncitable. Exercised
    against the real ON DELETE CASCADE rather than read off the DDL -- the schema is the
    thing being trusted here, so the schema is what has to run."""
    await ingest_ncd(session, StubEmbedder(), parse_ncd_response(FIXTURE))
    policies = await policies_repo.fetch_all(session)
    assert len(policies) == 1

    await session.execute("DELETE FROM policies WHERE id = $1", policies[0].id)

    assert await chunks_repo.count(session) == 0


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
    assert await policies_repo.find_by_document_version(session, "999", 1) is None

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
    stored = await policies_repo.find_by_document_version(session, "999", 1)
    assert stored is not None
    assert stored.document_id == "999"
