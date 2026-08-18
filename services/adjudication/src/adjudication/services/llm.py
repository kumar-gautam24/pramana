"""The model-provider abstraction -- ADR-0010 keeps the model a configuration choice,
so the eventual 14B-vs-32B-vs-hosted comparison is a config sweep rather than a
rewrite. `LLMProvider` is the seam extraction (and, later, judgment verification)
code call against; `OllamaClient` is the only implementation today, calling a local
Ollama server through an injected `httpx.AsyncClient` -- same reasons as
`policy_client` and `member_client`: a stub transport in tests, one shared connection
pool per case, and no retries hidden in this layer (see `upstream`'s docstring).

Does not implement ADR-0010's startup guard ("services refuse to start if the model
cannot produce schema-constrained output"): there is no Ollama on this development
machine to guard against yet, and nothing before Task 8 calls a model. Task 8 owns
the guard; adding it here would make this service unbootable in every environment
that exists today."""

import json
from typing import Any, Protocol

import httpx

from adjudication.services import upstream


class LLMProvider(Protocol):
    async def chat(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Any:
        """Send a schema-constrained chat completion and return the parsed JSON body
        the model produced. Implementations raise `UpstreamUnavailable` -- never a
        provider-specific exception -- so callers catch one type regardless of which
        model is configured."""
        ...


class OllamaClient:
    """Calls Ollama's `/api/chat` with `stream: false`, the caller's JSON schema in
    `format`, and `temperature: 0` -- invariant 7 (every decision is reproducible)
    rules out a sampled extraction, and this is the one place in the pipeline a
    model's output drives control flow (ADR-0003)."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, model: str) -> None:
        self._client = client
        self._base_url = base_url
        self._model = model

    async def chat(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Any:
        path = "/api/chat"
        response = await upstream.send(
            self._client,
            "llm",
            "POST",
            f"{self._base_url}{path}",
            json={
                "model": self._model,
                "messages": messages,
                "format": schema,
                "stream": False,
                "options": {"temperature": 0},
            },
        )
        # Ollama returns the schema-constrained answer as a JSON *string* inside
        # message.content, not as a nested object -- one more decode `build` must do,
        # so a malformed content string is caught by `upstream.parse` the same way a
        # malformed top-level body is for policy_client and member_client.
        return upstream.parse(
            "llm", response, path, lambda body: json.loads(body["message"]["content"])
        )
