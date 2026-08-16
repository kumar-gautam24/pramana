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


async def _dense(session: AsyncSession, vector: list[float], limit: int) -> list[int]:
    rows = await session.execute(
        select(Chunk.id).order_by(Chunk.embedding.cosine_distance(vector)).limit(limit)
    )
    return list(rows.scalars())


async def _lexical(session: AsyncSession, query: str, limit: int) -> list[int]:
    """Catches exact tokens like "AHI" and "Type IV" that embeddings blur."""
    tsquery = func.plainto_tsquery("english", query)
    rows = await session.execute(
        select(Chunk.id)
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(func.ts_rank(Chunk.tsv, tsquery).desc())
        .limit(limit)
    )
    return list(rows.scalars())


async def search(
    session: AsyncSession,
    embedder,
    reranker,
    query: str,
    on: date | None = None,
    limit: int = 5,
) -> list[Hit]:
    vector = embedder.encode([query])[0]
    fused = reciprocal_rank_fusion(
        [
            await _dense(session, vector, CANDIDATES),
            await _lexical(session, query, CANDIDATES),
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

    if on is not None:
        # Keep only chunks belonging to the version that governed the date of service. A
        # case judged by a policy that was not yet in force is wrong in the direction that
        # harms the member.
        versions: dict[str, dict[int, Policy]] = defaultdict(dict)
        for _, policy in rows:
            versions[policy.display_id][policy.id] = policy
        governing = {
            display: in_force_on(list(found.values()), on)
            for display, found in versions.items()
        }
        rows = [
            (chunk, policy)
            for chunk, policy in rows
            if (winner := governing.get(policy.display_id)) is not None
            and winner.id == policy.id
        ]

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
