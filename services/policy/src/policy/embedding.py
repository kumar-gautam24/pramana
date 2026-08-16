"""Local embedding.

The model is loaded once at startup rather than per request: loading it costs seconds, and
paying that on the first search blows past the caller's timeout."""

from fastembed import TextEmbedding

from policy.config import get_settings

DIMENSIONS = 384


class Embedder:
    def __init__(self, model_name: str | None = None) -> None:
        self._model = TextEmbedding(model_name or get_settings().embedding_model)

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(texts)]


class Reranker:
    """The cross-encoder.

    It is here to produce a score a threshold can be compared against, not to improve
    ranking -- it measurably costs ranking accuracy. See docs/decisions/0007. Removing it
    would improve the retrieval table and destroy the ability to refuse."""

    def __init__(self, model_name: str | None = None) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model_name or get_settings().rerank_model)

    def score(self, query: str, documents: list[str]) -> list[float]:
        return [float(s) for s in self._model.rerank(query, documents)]
