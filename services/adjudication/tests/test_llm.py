"""Tests for `OllamaClient` -- see `services/llm.py`. Stub transport only
(`httpx.MockTransport`): no live Ollama, no network -- matching `test_clients.py`'s
convention for the other two upstream clients."""

import json

import httpx
import pytest

from adjudication.services.llm import OllamaClient
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
