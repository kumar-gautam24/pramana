# ADR-0007 — The reranker exists to produce a score, not to improve ranking

**Status:** accepted, 2026-08-15. Carried from Deflect.

## Context

Measured in the predecessor project over 65 answerable items:

| variant | hit@5 | MRR |
| --- | --- | --- |
| dense only | 0.892 | 0.744 |
| hybrid | 0.892 | 0.762 |
| hybrid + rerank | 0.862 | 0.706 |

Reranking cost 3 points of hit@5 and 5.6 of MRR. Three stronger cross-encoders were tried;
none beat plain hybrid on MRR.

However, Reciprocal Rank Fusion scores carry no relevance information at all — the top-ranked
chunk scores `1/(k+1)` regardless of whether it is relevant, so every query produced an
identical 0.0164. Median separation between answerable and unanswerable questions:

| score source | separation |
| --- | --- |
| RRF fused | 0.0000 |
| cross-encoder | 5.6804 |

## Decision

Keep the cross-encoder. It is the only stage producing a calibrated score that a threshold
can be compared against.

## Consequences

Retrieval ranking is marginally worse and the ability to withhold judgment exists at all.
For a system whose entire purpose is knowing when not to assert, that trade is correct.

`ms-marco-MiniLM-L-6-v2` is kept over `jina-reranker-v1-turbo-en` despite jina's better
hit@5: median separation 5.68 against 0.78 makes the threshold far less sensitive to where
it is set.

**Do not "optimise" the reranker away.** Removing it would improve the retrieval table and
destroy the gate.
