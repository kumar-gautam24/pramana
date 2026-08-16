"""Hybrid retrieval: dense similarity, lexical matching, fused, then reranked.

Dense search understands meaning; lexical search catches exact tokens like "AHI" and
"Type IV" that embeddings blur. Fusing them uses agreement between two different notions
of relevance."""

from collections import defaultdict
from datetime import date

from pramana_common.schemas import Hit

from policy.dating import in_force_on
from policy.models import Policy
from policy.repositories import chunks as chunks_repo
from policy.repositories import policies as policies_repo

#: How many chunks survive fusion into reranking. Wider costs latency; narrower drops
#: documents the cross-encoder would have promoted.
CANDIDATES = 20


def reciprocal_rank_fusion(
    rankings: list[list[int]], k: int = 60
) -> list[tuple[int, float]]:
    """Combine rankings by reciprocal rank.

    The score depends only on position, never on the underlying similarity, so it says
    nothing about whether the top result is actually relevant. That is why the escalation
    gate is built on the cross-encoder score instead -- see docs/decisions/0007."""
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for position, chunk_id in enumerate(ranking):
            scores[chunk_id] += 1.0 / (k + position + 1)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


async def _governing_policy_ids(conn, on: date) -> list[int]:
    """The policy ids in force on `on`, resolved from every version -- not just the ones
    that happen to survive retrieval.

    Resolving after the candidate cut would let an un-retrieved version silently hand
    governance to a superseded one: if the current version's chunks never made the top
    `CANDIDATES`, `in_force_on` would see only stale versions and declare one of them the
    winner. Fetching the (tiny) policies table directly avoids that.

    Versions of one document are grouped by `document_id`, the key the corpus is unique
    on -- see the `(document_id, document_version)` constraint in
    migrations/0001_policies_and_chunks.sql. `display_id` is the human label and CMS
    renumbers it across revisions, so grouping by it would split one document's history
    into two lineages and let both of them govern the same date."""
    policies = await policies_repo.fetch_all(conn)
    by_document: dict[str, list[Policy]] = defaultdict(list)
    for policy in policies:
        by_document[policy.document_id].append(policy)

    winners = (in_force_on(versions, on) for versions in by_document.values())
    return [winner.id for winner in winners if winner is not None]


async def search(
    conn,
    embedder,
    reranker,
    query: str,
    on: date | None = None,
    limit: int = 5,
) -> list[Hit]:
    # Resolve the governing version before retrieval, not after: candidates are cut down
    # to CANDIDATES before this filter would otherwise run, and a superseded version
    # dominating that cut must not be able to pass itself off as governing.
    policy_ids: list[int] | None = None
    if on is not None:
        policy_ids = await _governing_policy_ids(conn, on)
        if not policy_ids:
            return []

    vector = embedder.encode([query])[0]
    fused = reciprocal_rank_fusion(
        [
            await chunks_repo.dense_search(conn, vector, CANDIDATES, policy_ids),
            await chunks_repo.lexical_search(conn, query, CANDIDATES, policy_ids),
        ]
    )
    ids = [chunk_id for chunk_id, _ in fused[:CANDIDATES]]
    if not ids:
        return []

    # Rerank scores tie, and the sort below is stable, so without the repository's
    # explicit ORDER BY chunks.id the surviving hit would be whichever row Postgres
    # happened to return first. Cited evidence has to be the same on every run --
    # CLAUDE.md 7.
    rows = await chunks_repo.fetch_with_policies(conn, ids)
    if not rows:
        return []

    scores = reranker.score(query, [chunk.text for chunk, _ in rows])
    ranked = sorted(zip(rows, scores, strict=True), key=lambda pair: -pair[1])[:limit]

    return [
        Hit(
            chunk_id=chunk.id,
            policy_id=policy.id,
            display_id=policy.display_id,
            heading_path=chunk.heading_path,
            text=chunk.text,
            score=score,
        )
        for (chunk, policy), score in ranked
    ]
