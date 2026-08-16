"""Reciprocal rank fusion: combining two candidate rankings into one, by position alone.

Dense search understands meaning; lexical search catches exact tokens like "AHI" and
"Type IV" that embeddings blur. Fusing them uses agreement between two different notions
of relevance -- see policy.services.search for how the fused list is retrieved and
reranked."""

from collections import defaultdict

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
