"""Tests for the model providers -- see `services/llm.py`. Stub transport only
(`httpx.MockTransport`): no live Ollama, no Gemini call, no network -- matching
`test_clients.py`'s convention for the other two upstream clients.

Both providers are held to the same three promises, because `extract` holds an
`LLMProvider` and cannot tell which one it has: temperature is pinned to 0, the
caller's schema reaches the wire unmodified, and every failure arrives as
`UpstreamUnavailable` rather than a provider-specific exception."""

import json

import httpx
import pytest

from adjudication.config import Provider, Settings
from adjudication.services.llm import GeminiClient, OllamaClient, build_provider
from adjudication.services.upstream import UpstreamUnavailable

BASE_URL = "http://testserver"
MODEL = "qwen2.5:14b-instruct"


def _client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=5.0)


async def test_chat_sends_stream_false_temperature_zero_and_the_schema():
    """Invariant 7 (every decision is reproducible) is why temperature is pinned to
    0 -- a sampled extraction would not be."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": json.dumps({"sets": []})}}
        )

    schema = {"type": "object", "properties": {"sets": {"type": "array"}}}
    async with _client(handler) as client:
        await OllamaClient(client, BASE_URL, MODEL).chat(
            [{"role": "user", "content": "hi"}], schema
        )

    assert captured["body"]["model"] == MODEL
    assert captured["body"]["stream"] is False
    assert captured["body"]["options"]["temperature"] == 0
    assert captured["body"]["format"] == schema
    assert captured["body"]["messages"] == [{"role": "user", "content": "hi"}]


async def test_chat_parses_the_json_string_in_message_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"sets": [{"criteria": []}]}),
                }
            },
        )

    async with _client(handler) as client:
        result = await OllamaClient(client, BASE_URL, MODEL).chat([], {})

    assert result == {"sets": [{"criteria": []}]}


async def test_chat_non_json_content_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "not json"}})

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable) as exc_info:
            await OllamaClient(client, BASE_URL, MODEL).chat([], {})

    assert exc_info.value.service == "llm"
    assert "unparseable" in exc_info.value.detail


async def test_chat_missing_message_key_raises_upstream_unavailable():
    """A 200 whose body doesn't have the shape `OllamaClient` expects must not
    escape as a bare `KeyError` -- the pipeline only ever catches
    `UpstreamUnavailable`."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable) as exc_info:
            await OllamaClient(client, BASE_URL, MODEL).chat([], {})

    assert exc_info.value.service == "llm"


async def test_chat_500_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable):
            await OllamaClient(client, BASE_URL, MODEL).chat([], {})


async def test_chat_timeout_raises_upstream_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable):
            await OllamaClient(client, BASE_URL, MODEL).chat([], {})


# --- GeminiClient ------------------------------------------------------------------

API_KEY = "test-key"
GEMINI_MODEL = "gemini-2.5-pro"


def _gemini_response(payload: object) -> httpx.Response:
    return httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]},
    )


async def test_gemini_sends_the_schema_unmodified_at_temperature_zero():
    """`responseJsonSchema`, not `responseSchema`: the schema `extract` builds contains
    `$defs`/`$ref` and a free-form `params` object, neither of which the cut-down
    OpenAPI dialect accepts. If this assertion is ever relaxed to a translated schema,
    this provider has started constraining the model differently from the others."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = request.headers
        captured["url"] = str(request.url)
        return _gemini_response({"sets": []})

    schema = {
        "$defs": {"C": {"type": "object", "properties": {"text": {"type": "string"}}}},
        "type": "object",
        "properties": {"sets": {"type": "array", "items": {"$ref": "#/$defs/C"}}},
    }
    async with _client(handler) as client:
        await GeminiClient(client, BASE_URL, GEMINI_MODEL, API_KEY).chat(
            [{"role": "user", "content": "hi"}], schema
        )

    config = captured["body"]["generationConfig"]
    assert config["responseJsonSchema"] == schema
    assert "responseSchema" not in config
    assert config["temperature"] == 0
    assert config["responseMimeType"] == "application/json"
    assert captured["url"].endswith(f"/v1beta/models/{GEMINI_MODEL}:generateContent")


async def test_gemini_carries_the_key_in_a_header_not_the_url():
    """A key in the query string lands in every access log and proxy trace between here
    and Google. It belongs in a header."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["url"] = str(request.url)
        return _gemini_response({"sets": []})

    async with _client(handler) as client:
        await GeminiClient(client, BASE_URL, GEMINI_MODEL, API_KEY).chat([], {})

    assert captured["headers"]["x-goog-api-key"] == API_KEY
    assert API_KEY not in captured["url"]


async def test_gemini_moves_system_turns_into_system_instruction():
    """The chat protocol carries one list; Gemini splits it. A system turn left in
    `contents` would be read as the member's own words."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _gemini_response({"sets": []})

    async with _client(handler) as client:
        await GeminiClient(client, BASE_URL, GEMINI_MODEL, API_KEY).chat(
            [
                {"role": "system", "content": "you extract criteria"},
                {"role": "user", "content": "the policy text"},
            ],
            {},
        )

    assert captured["body"]["systemInstruction"] == {
        "parts": [{"text": "you extract criteria"}]
    }
    assert captured["body"]["contents"] == [
        {"role": "user", "parts": [{"text": "the policy text"}]}
    ]


async def test_gemini_drops_thought_parts_before_parsing():
    """A 2.5 model can return its reasoning as a sibling part. Taking parts[0] would
    hand a paragraph of deliberation to `json.loads`."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "first I will consider...", "thought": True},
                                {"text": json.dumps({"sets": [{"criteria": []}]})},
                            ]
                        }
                    }
                ]
            },
        )

    async with _client(handler) as client:
        result = await GeminiClient(client, BASE_URL, GEMINI_MODEL, API_KEY).chat([], {})

    assert result == {"sets": [{"criteria": []}]}


async def test_gemini_surfaces_the_api_error_message():
    """"status 400" alone does not say whether the key is bad, the quota is gone, or
    the schema was rejected -- and those have three different fixes."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"code": 400, "message": "API key not valid."}}
        )

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable) as excinfo:
            await GeminiClient(client, BASE_URL, GEMINI_MODEL, API_KEY).chat([], {})

    assert "API key not valid." in excinfo.value.detail
    assert "400" in excinfo.value.detail


async def test_gemini_error_without_a_parseable_body_still_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream connect error")

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable) as excinfo:
            await GeminiClient(client, BASE_URL, GEMINI_MODEL, API_KEY).chat([], {})

    assert "503" in excinfo.value.detail


async def test_gemini_truncated_answer_raises_rather_than_returning_half_a_case():
    """A response cut off at the token ceiling is invalid JSON. It must not reach
    `extract` as a partially-populated set."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": '{"sets": [{"crit'}]}}]},
        )

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable):
            await GeminiClient(client, BASE_URL, GEMINI_MODEL, API_KEY).chat([], {})


async def test_gemini_response_missing_candidates_raises():
    """A safety block returns a 200 with no candidates. Silence is not an extraction."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})

    async with _client(handler) as client:
        with pytest.raises(UpstreamUnavailable):
            await GeminiClient(client, BASE_URL, GEMINI_MODEL, API_KEY).chat([], {})


# --- build_provider ----------------------------------------------------------------


def _settings(**overrides) -> Settings:
    base = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "redis_url": "redis://localhost:6379",
        "policy_url": "http://localhost:8001",
        "member_url": "http://localhost:8005",
        "llm_url": BASE_URL,
        "llm_model": MODEL,
    }
    return Settings(**(base | overrides))


def test_build_provider_defaults_to_ollama():
    assert isinstance(build_provider(_settings(), httpx.AsyncClient()), OllamaClient)


def test_build_provider_returns_gemini_when_configured():
    settings = _settings(
        llm_provider=Provider.GEMINI, llm_model=GEMINI_MODEL, gemini_api_key=API_KEY
    )
    assert isinstance(build_provider(settings, httpx.AsyncClient()), GeminiClient)


def test_an_unknown_provider_name_is_rejected_at_settings_time():
    """A typo in LLM_PROVIDER must not fall through to a default and adjudicate cases
    against a model nobody chose."""
    with pytest.raises(ValueError):
        _settings(llm_provider="gemeni")


def test_gemini_without_a_key_is_rejected_at_settings_time():
    """Discovered at startup, not mid-case: a case escalated for a missing API key
    reads as a fact about the member's record, which it is not."""
    with pytest.raises(ValueError) as excinfo:
        _settings(llm_provider=Provider.GEMINI)

    assert "GEMINI_API_KEY" in str(excinfo.value)
