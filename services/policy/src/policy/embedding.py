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
