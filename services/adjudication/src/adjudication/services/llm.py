"""The model-provider abstraction -- ADR-0010 keeps the model a configuration choice,
so the eventual 14B-vs-32B-vs-hosted comparison is a config sweep rather than a
rewrite. `LLMProvider` is the seam extraction (and judgment verification) code calls
against; `OllamaClient` and `GeminiClient` implement it, and `build_provider` picks
between them from `Settings.llm_provider`. Both take an injected `httpx.AsyncClient`
-- same reasons as `policy_client` and `member_client`: a stub transport in tests,
one shared connection pool per case, and no retries hidden in this layer (see
`upstream`'s docstring).

Adding a third provider means one class implementing `chat` and one arm in
`build_provider`. Nothing above this module names a provider: `extract` and the
judgment verifier receive an `LLMProvider` and cannot tell which one they hold.

Does not implement ADR-0010's startup guard ("services refuse to start if the model
cannot produce schema-constrained output"): there is no Ollama on this development
machine to guard against yet, and nothing before Task 8 calls a model. Task 8 owns
the guard; adding it here would make this service unbootable in every environment
that exists today."""

import json
from typing import Any, Protocol

import httpx

from adjudication.config import Provider, Settings
from adjudication.services import upstream
from adjudication.services.upstream import UpstreamUnavailable


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


def _gemini_error_detail(response: httpx.Response) -> str:
    """Google's error body carries the actual complaint -- an unsupported schema
    keyword, a revoked key, an exhausted quota -- and `upstream.parse` would reduce all
    of them to "status 400". Surfacing the message is the difference between a
    ten-minute fix and an afternoon, and it is safe to log: the key travels in a header
    and never appears in the body."""
    try:
        return f"status {response.status_code}: {response.json()['error']['message']}"
    except (ValueError, KeyError, TypeError):
        return f"status {response.status_code}"


def _gemini_answer(body: object) -> Any:
    """Pull the JSON answer out of the first candidate.

    Parts are joined rather than indexed at [0], and parts flagged `thought` are
    dropped: a thinking model returns its reasoning as sibling parts, and taking
    parts[0] blindly would hand the caller a paragraph of deliberation to
    `json.loads`."""
    parts = body["candidates"][0]["content"]["parts"]  # type: ignore[index]
    text = "".join(part["text"] for part in parts if not part.get("thought"))
    return json.loads(text)


class GeminiClient:
    """Calls Gemini's `generateContent` with the caller's schema and temperature 0 --
    invariant 7 (every decision is reproducible) rules out a sampled extraction, and
    this is the one place in the pipeline a model's output drives control flow
    (ADR-0003).

    The schema goes in `responseJsonSchema`, not `responseSchema`. The latter takes a
    cut-down OpenAPI dialect that rejects `$defs`/`$ref` and cannot express a free-form
    object -- and `extract._build_schema` produces both, because it is generated from
    nested pydantic models and `params` is deliberately open (its shape depends on the
    criterion type, and `domain.params.validate_params` is what closes it). Translating
    the schema down to that dialect would mean this provider silently constrains the
    model differently from the others, which is precisely the drift a provider
    abstraction exists to prevent. `responseJsonSchema` takes the schema as written.

    Context caching is deliberately not used. The system prompt is large enough to be a
    candidate, but a cache is a second thing that can be stale while looking fresh, and
    an audit that cannot say which bytes the model actually saw is worth less than the
    tokens it saves."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, model: str, api_key: str) -> None:
        self._client = client
        self._base_url = base_url
        self._model = model
        self._api_key = api_key

    async def chat(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> Any:
        # Gemini splits what the chat protocol carries as one list: system turns go in
        # `systemInstruction`, and the rest alternate user/model. Concatenating the
        # system turns preserves their order for the case where a caller sends more
        # than one.
        instructions = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
            if m["role"] != "system"
        ]

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
                # A 2.5 model spends output budget on reasoning before it writes the
                # answer, so the default ceiling can truncate a long extraction into
                # invalid JSON. Failing on a real limit is fine; failing because the
                # budget was left at its default is not.
                "maxOutputTokens": 8192,
            },
        }
        if instructions:
            payload["systemInstruction"] = {"parts": [{"text": instructions}]}

        path = f"/v1beta/models/{self._model}:generateContent"
        response = await upstream.send(
            self._client,
            "llm",
            "POST",
            f"{self._base_url}{path}",
            headers={"x-goog-api-key": self._api_key},
            json=payload,
        )
        if response.status_code // 100 != 2:
            raise UpstreamUnavailable("llm", _gemini_error_detail(response))
        return upstream.parse("llm", response, path, _gemini_answer)


def build_provider(settings: Settings, client: httpx.AsyncClient) -> LLMProvider:
    """The one place a provider name becomes a provider. Callers hold `LLMProvider`."""
    if settings.llm_provider is Provider.OLLAMA:
        return OllamaClient(client, settings.llm_url, settings.llm_model)

    # Settings' validator already rejected a gemini configuration with no key, so this
    # is unreachable rather than defensive -- asserted, not handled, because a None key
    # would otherwise reach httpx as a header value and fail somewhere far less obvious.
    assert settings.gemini_api_key is not None
    return GeminiClient(client, settings.llm_url, settings.llm_model, settings.gemini_api_key)
