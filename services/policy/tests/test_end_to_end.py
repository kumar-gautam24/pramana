"""The whole chain on the real recorded document: parse -> chunk -> ingest -> search.

Every other test in this suite exercises one stage against input it constructs itself, so
a stage that agrees with its own fixture but not with the next one passes everything. Both
Criticals this branch shipped lived in exactly that gap. What is asserted here is that a
criterion a reviewer would actually cite survives the whole chain, under the heading it
lives under, and that it comes from the version governing the date of service.

The parser's dropped-tail Critical is *not* reachable from this document -- no heading in
it is followed by prose in the same block, verified by re-introducing the bug and watching
this file still pass. That one stays covered by test_parsing.py; the version Critical is
covered here, on real content."""

import json
import re
import zlib
from dataclasses import replace
from datetime import date
from pathlib import Path

from policy.services.cms import parse_ncd_response
from policy.services.ingest import ingest_ncd
from policy.services.search import search

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ncd-226.json").read_text())

#: The AHI threshold in NCD 240.4's covered indications. Named here as data, not branched
#: on anywhere in the service -- see CLAUDE.md 3.
CRITERION = "greater than or equal to 15 events per hour"
HEADING = "Nationally Covered Indications"


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class HashingEmbedder:
    """A hashed bag of words: deterministic, offline, and instant. The real model is not
    what this test is about, but a constant vector would collapse dense retrieval into row
    order, which is a different test again. crc32 rather than hash() -- str hashing is
    salted per process, so the vectors would differ between runs."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * 384
            for token in _tokens(text):
                vector[zlib.crc32(token.encode()) % 384] += 1.0
            vectors.append(vector)
        return vectors


class OverlapReranker:
    """Scores by how many of the query's terms a chunk contains. Crude, but it ranks on
    the chunk's own content rather than on the phrase the assertion looks for, so the
    test cannot pass by picking the answer it was told to find."""

    def score(self, query: str, documents: list[str]) -> list[float]:
        terms = set(_tokens(query))
        return [float(len(terms & set(_tokens(document)))) for document in documents]


async def test_a_real_criterion_is_retrievable_under_its_own_heading(db_session):
    records = parse_ncd_response(FIXTURE)
    result = await ingest_ncd(db_session, HashingEmbedder(), records)
    assert result.policies_added == 1

    hits = await search(
        db_session,
        HashingEmbedder(),
        OverlapReranker(),
        "AHI greater than or equal to 15 events per hour",
        on=date(2026, 1, 15),
        limit=5,
    )

    assert hits, "the governing policy on a covered date returned nothing"
    cited = [hit for hit in hits if CRITERION in hit.text]
    assert cited, f"the AHI criterion did not survive the chain: {[h.text for h in hits]}"
    # The heading is half the point: a hit a reviewer cannot open is not evidence.
    assert any(HEADING in hit.heading_path for hit in cited)
    assert all(hit.display_id == records[0].display_id for hit in hits)


async def test_only_the_governing_version_of_the_real_document_is_cited(db_session):
    """The second Critical this branch shipped, on real content: a superseded version of
    the same document must never supply the citation. Its display label is renumbered
    here because CMS does renumber across revisions -- versions are one document by
    `document_id`, and grouping them by the label would let both versions govern."""
    current = parse_ncd_response(FIXTURE)[0]
    superseded = replace(
        current,
        document_version=current.document_version - 1,
        display_id="240.3",
        effective_from=date(2005, 1, 1),
        # Open-ended, because that is how CMS leaves a superseded version: the end date
        # reads "N/A". Both versions therefore cover the date of service and only the
        # later effective_from separates them -- which is the whole job of in_force_on.
        effective_to=None,
    )
    await ingest_ncd(db_session, HashingEmbedder(), [superseded, current])

    hits = await search(
        db_session,
        HashingEmbedder(),
        OverlapReranker(),
        "AHI greater than or equal to 15 events per hour",
        on=date(2026, 1, 15),
        limit=5,
    )

    assert hits
    assert {hit.display_id for hit in hits} == {current.display_id}


async def test_a_date_before_the_policy_existed_retrieves_nothing(db_session):
    """The same chain, one date earlier than the determination itself. Coverage that did
    not exist on the date of service must escalate, never fall back to today's rule."""
    await ingest_ncd(db_session, HashingEmbedder(), parse_ncd_response(FIXTURE))

    hits = await search(
        db_session,
        HashingEmbedder(),
        OverlapReranker(),
        "AHI greater than or equal to 15 events per hour",
        on=date(2001, 1, 1),
        limit=5,
    )

    assert hits == []
