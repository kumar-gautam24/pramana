from datetime import date

import pytest

from policy.models import Chunk, Policy
from policy.retrieval import reciprocal_rank_fusion, search

#: A fixed dimension-384 vector reused everywhere. Dense ranking is not the thing under
#: test here -- correctness of the date filter turns on the SQL-level policy_id
#: restriction in `search`, not on embedding similarity.
EMBED = [0.0] * 384


class StubEmbedder:
    """Deterministic and instant; see StubEmbedder in test_ingest.py for the same pattern."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [EMBED for _ in texts]


class ScoreByText:
    """Maps a chunk's text to a score the test chose, so a returned Hit's score can be
    checked against exactly what the reranker produced -- not a value derived from RRF
    position, which is the failure mode `search` exists to avoid."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    def score(self, query: str, documents: list[str]) -> list[float]:
        return [self._scores[document] for document in documents]


async def _policy(
    session,
    *,
    document_id: str,
    version: int,
    display_id: str,
    effective_from: date,
    effective_to: date | None,
) -> Policy:
    policy = Policy(
        document_id=document_id,
        document_version=version,
        display_id=display_id,
        title="Test Policy",
        effective_from=effective_from,
        effective_to=effective_to,
        benefit_category="",
        source_url="https://example.invalid/test",
    )
    session.add(policy)
    await session.flush()
    return policy


async def _chunk(session, policy: Policy, ordinal: int, text: str) -> Chunk:
    chunk = Chunk(
        policy_id=policy.id,
        ordinal=ordinal,
        heading_path="Root > Section",
        text=text,
        embedding=EMBED,
    )
    session.add(chunk)
    await session.flush()
    return chunk


def test_fuses_two_rankings():
    dense = [10, 20, 30]
    lexical = [30, 10, 40]

    fused = reciprocal_rank_fusion([dense, lexical])
    order = [chunk_id for chunk_id, _ in fused]

    assert order[0] == 10
    assert set(order) == {10, 20, 30, 40}


def test_a_document_ranked_well_by_both_beats_one_ranked_well_by_either():
    """This is the whole point of fusing: agreement across two different notions of
    relevance outranks a strong showing in one."""
    fused = dict(reciprocal_rank_fusion([[1, 2], [1, 3]]))

    assert fused[1] > fused[2]
    assert fused[1] > fused[3]


def test_scores_descend():
    fused = reciprocal_rank_fusion([[5, 6, 7], [7, 6, 5]])
    scores = [score for _, score in fused]

    assert scores == sorted(scores, reverse=True)


def test_an_empty_ranking_contributes_nothing():
    assert reciprocal_rank_fusion([[1, 2], []]) == reciprocal_rank_fusion([[1, 2]])


def test_no_rankings_yields_nothing():
    assert reciprocal_rank_fusion([]) == []


def test_fused_scores_carry_no_relevance_information():
    """The top-ranked chunk always scores 1/(k+1) regardless of whether it is relevant,
    which is exactly why the gate cannot threshold on an RRF score and the cross-encoder
    has to stay. See docs/decisions/0007."""
    one = reciprocal_rank_fusion([[42]])
    other = reciprocal_rank_fusion([[99]])

    # The concrete value, not just agreement between the two: comparing them to each
    # other alone passes for any implementation, including one that returns a real
    # similarity, which is exactly the claim being denied here.
    assert one[0][1] == pytest.approx(1 / 61)
    assert other[0][1] == pytest.approx(1 / 61)


async def test_a_superseded_version_never_governs_even_when_it_fills_the_candidate_window(
    db_session,
):
    """The bug this guards against: an open-ended old version with many chunks can win
    every dense/lexical slot before the date filter ever runs, so resolving `in_force_on`
    from the retrieved candidates -- instead of from every version -- would see only
    stale chunks and crown one of them governing. Restricting retrieval to the
    already-resolved governing policy id is what prevents that."""
    old = await _policy(
        db_session,
        document_id="900",
        version=1,
        display_id="900.1",
        effective_from=date(2008, 1, 1),
        effective_to=None,
    )
    for i in range(25):
        await _chunk(db_session, old, i, f"zephyrgadget superseded chunk {i}")

    new = await _policy(
        db_session,
        document_id="900",
        version=2,
        display_id="900.1",
        effective_from=date(2020, 1, 1),
        effective_to=None,
    )
    current = await _chunk(db_session, new, 0, "zephyrgadget current chunk")

    hits = await search(
        db_session,
        StubEmbedder(),
        ScoreByText({current.text: 1.0}),
        "zephyrgadget",
        on=date(2021, 6, 1),
        limit=5,
    )

    assert len(hits) == 1
    assert hits[0].policy_id == new.id
    assert hits[0].chunk_id == current.id


async def test_a_renumbered_display_id_does_not_split_one_document_into_two_lineages(
    db_session,
):
    """Versions of one document must be grouped by `document_id` -- the key the corpus is
    unique on -- not by `display_id`, which CMS renumbers across revisions. Grouped by the
    label, these two rows look like two separate documents, each resolves as governing on
    its own, and the superseded version comes back alongside the current one."""
    old = await _policy(
        db_session,
        document_id="777",
        version=1,
        display_id="240.4",
        effective_from=date(2008, 1, 1),
        effective_to=None,
    )
    superseded = await _chunk(db_session, old, 0, "zephyrgadget superseded chunk")

    new = await _policy(
        db_session,
        document_id="777",
        version=2,
        display_id="240.4.1",
        effective_from=date(2020, 1, 1),
        effective_to=None,
    )
    current = await _chunk(db_session, new, 0, "zephyrgadget current chunk")

    hits = await search(
        db_session,
        StubEmbedder(),
        ScoreByText({superseded.text: 2.0, current.text: 1.0}),
        "zephyrgadget",
        on=date(2021, 6, 1),
        limit=5,
    )

    assert [hit.chunk_id for hit in hits] == [current.id]


async def test_a_date_before_any_version_returns_no_hits(db_session):
    old = await _policy(
        db_session,
        document_id="901",
        version=1,
        display_id="901.1",
        effective_from=date(2008, 1, 1),
        effective_to=None,
    )
    await _chunk(db_session, old, 0, "zephyrgadget only chunk")

    hits = await search(
        db_session,
        StubEmbedder(),
        ScoreByText({}),
        "zephyrgadget",
        on=date(2001, 1, 1),
        limit=5,
    )

    assert hits == []


async def test_no_date_filter_searches_every_version(db_session):
    old = await _policy(
        db_session,
        document_id="902",
        version=1,
        display_id="902.1",
        effective_from=date(2008, 1, 1),
        effective_to=date(2019, 12, 31),
    )
    old_chunk = await _chunk(db_session, old, 0, "zephyrgadget old chunk")

    new = await _policy(
        db_session,
        document_id="902",
        version=2,
        display_id="902.1",
        effective_from=date(2020, 1, 1),
        effective_to=None,
    )
    new_chunk = await _chunk(db_session, new, 0, "zephyrgadget new chunk")

    hits = await search(
        db_session,
        StubEmbedder(),
        ScoreByText({old_chunk.text: 1.0, new_chunk.text: 2.0}),
        "zephyrgadget",
        on=None,
        limit=5,
    )

    assert {hit.policy_id for hit in hits} == {old.id, new.id}


async def test_hit_score_is_the_rerankers_score_not_the_fused_score(db_session):
    policy = await _policy(
        db_session,
        document_id="903",
        version=1,
        display_id="903.1",
        effective_from=date(2008, 1, 1),
        effective_to=None,
    )
    chunk = await _chunk(db_session, policy, 0, "zephyrgadget scored chunk")

    hits = await search(
        db_session,
        StubEmbedder(),
        ScoreByText({chunk.text: 0.4242}),
        "zephyrgadget",
        on=None,
        limit=5,
    )

    assert hits[0].score == pytest.approx(0.4242)
