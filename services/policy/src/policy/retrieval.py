"""Hybrid retrieval: dense similarity, lexical matching, fused, then reranked.

Dense search understands meaning; lexical search catches exact tokens like "AHI" and
"Type IV" that embeddings blur. Fusing them uses agreement between two different notions
of relevance."""

from collections import defaultdict
from datetime import date

from pramana_common.schemas import Hit
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from policy.dating import in_force_on
from policy.models import Chunk, Policy

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


async def _governing_policy_ids(session: AsyncSession, on: date) -> list[int]:
    """The policy ids in force on `on`, resolved from every version -- not just the ones
    that happen to survive retrieval.

    Resolving after the candidate cut would let an un-retrieved version silently hand
    governance to a superseded one: if the current version's chunks never made the top
    `CANDIDATES`, `in_force_on` would see only stale versions and declare one of them the
    winner. Querying the (tiny) policies table directly avoids that.

    Versions of one document are grouped by `document_id`, the key the corpus is unique
    on -- see the `(document_id, document_version)` constraint on Policy. `display_id` is
    the human label and CMS renumbers it across revisions, so grouping by it would split
    one document's history into two lineages and let both of them govern the same date."""
    policies = (await session.execute(select(Policy))).scalars().all()
    by_document: dict[str, list[Policy]] = defaultdict(list)
    for policy in policies:
        by_document[policy.document_id].append(policy)

    winners = (in_force_on(versions, on) for versions in by_document.values())
    return [winner.id for winner in winners if winner is not None]


async def _dense(
    session: AsyncSession,
    vector: list[float],
    limit: int,
    policy_ids: list[int] | None,
) -> list[int]:
    stmt = select(Chunk.id).order_by(Chunk.embedding.cosine_distance(vector)).limit(limit)
    if policy_ids is not None:
        stmt = stmt.where(Chunk.policy_id.in_(policy_ids))
    rows = await session.execute(stmt)
    return list(rows.scalars())


async def _lexical(
    session: AsyncSession,
    query: str,
    limit: int,
    policy_ids: list[int] | None,
) -> list[int]:
    """Catches exact tokens like "AHI" and "Type IV" that embeddings blur."""
    tsquery = func.plainto_tsquery("english", query)
    stmt = (
        select(Chunk.id)
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(func.ts_rank(Chunk.tsv, tsquery).desc())
        .limit(limit)
    )
    if policy_ids is not None:
        stmt = stmt.where(Chunk.policy_id.in_(policy_ids))
    rows = await session.execute(stmt)
    return list(rows.scalars())


async def search(
    session: AsyncSession,
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
        policy_ids = await _governing_policy_ids(session, on)
        if not policy_ids:
            return []

    vector = embedder.encode([query])[0]
    fused = reciprocal_rank_fusion(
        [
            await _dense(session, vector, CANDIDATES, policy_ids),
            await _lexical(session, query, CANDIDATES, policy_ids),
        ]
    )
    ids = [chunk_id for chunk_id, _ in fused[:CANDIDATES]]
    if not ids:
        return []

    rows = (
        await session.execute(
            select(Chunk, Policy)
            .join(Policy, Chunk.policy_id == Policy.id)
            .where(Chunk.id.in_(ids))
        )
    ).all()
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
