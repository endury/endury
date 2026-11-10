"""Tests for endury.coach.llm — the unified OpenRouter client and factories."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError
from openai.types import CompletionUsage

from endury.coach.llm import (
    STRIPPED_REQUEST_HEADERS,
    LLMCallMetadata,
    OpenRouterClient,
    _message_to_dict,
    create_llm,
    default_http_client,
    strip_stainless,
)
from endury.wiring.config import CoachConfig

# Fake OpenAI SDK doubles


def _fn(name, arguments):
    return SimpleNamespace(name=name, arguments=arguments)


def _tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=_fn(name, arguments))


def _response(content="", tool_calls=None, usage=None, response_id="resp-1"):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], usage=usage, id=response_id)


class _FakeCompletions:
    """Stub of ``client.chat.completions`` with scripted create() behaviour."""

    def __init__(self):
        self.calls = []
        self._result = None
        self._error_queue = []

    def set_result(self, result):
        self._result = result

    def queue_errors(self, *errors):
        self._error_queue = list(errors)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error_queue:
            raise self._error_queue.pop(0)
        result = self._result
        return result() if callable(result) else result


class _FakeSDK:
    """Stub OpenAI SDK object exposing ``.chat.completions``."""

    def __init__(self):
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


def _make_client(**overrides):
    sdk = _FakeSDK()
    kwargs = {
        "api_key": "test-key",
        "model": "cheap-model",
        "max_retries": 2,
        "retry_backoff_ms": 0,
        "client": sdk,
    }
    kwargs.update(overrides)
    return OpenRouterClient(**kwargs), sdk.completions


def _status_error(code):
    return APIStatusError(
        "boom",
        response=SimpleNamespace(status_code=code, headers={}, request=None),
        body=None,
    )


def _conn_error():
    return APIConnectionError(request=None)


# _message_to_dict


def test_message_to_dict_plain_text():
    msg = SimpleNamespace(content="Nice work.", tool_calls=None)
    assert _message_to_dict(msg) == {"role": "assistant", "content": "Nice work."}


def test_message_to_dict_none_content():
    msg = SimpleNamespace(content=None, tool_calls=None)
    assert _message_to_dict(msg)["content"] == ""


def test_message_to_dict_with_tool_calls():
    msg = SimpleNamespace(
        content="",
        tool_calls=[_tool_call("call-1", "get_activities", '{"days": 7}')],
    )
    result = _message_to_dict(msg)
    assert result["tool_calls"][0]["id"] == "call-1"
    assert result["tool_calls"][0]["type"] == "function"
    assert result["tool_calls"][0]["function"]["name"] == "get_activities"
    assert result["tool_calls"][0]["function"]["arguments"] == '{"days": 7}'


def test_message_to_dict_tool_call_none_arguments():
    msg = SimpleNamespace(content="", tool_calls=[_tool_call("c1", "noop", None)])
    result = _message_to_dict(msg)
    assert result["tool_calls"][0]["function"]["arguments"] == ""


# complete()


@pytest.mark.asyncio
async def test_complete_returns_assistant_dict():
    client, completions = _make_client()
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=5, cost=0.002)
    completions.set_result(_response(content="Hello!", usage=usage))

    result = await client.complete([{"role": "user", "content": "hi"}])

    assert result == {"role": "assistant", "content": "Hello!"}
    assert client.last_metadata.model == "cheap-model"
    assert client.last_metadata.input_tokens == 11
    assert client.last_metadata.output_tokens == 5
    assert client.last_metadata.estimated_cost_usd == 0.002
    assert client.last_metadata.request_id == "resp-1"


@pytest.mark.asyncio
async def test_complete_passes_tools_and_model_override():
    client, completions = _make_client()
    completions.set_result(_response(content="ok"))
    tools = [{"type": "function", "function": {"name": "t"}}]

    await client.complete(
        [{"role": "user", "content": "x"}], tools=tools, model="strong-model"
    )

    call = completions.calls[-1]
    assert call["model"] == "strong-model"
    assert call["tools"] == tools
    assert "stream" not in call


@pytest.mark.asyncio
async def test_complete_omits_reasoning_by_default():
    client, completions = _make_client()
    completions.set_result(_response(content="ok"))

    await client.complete([{"role": "user", "content": "x"}])

    assert completions.calls[-1]["extra_body"] == {
        "provider": {"data_collection": "deny", "zdr": True}
    }


@pytest.mark.asyncio
async def test_complete_passes_reasoning_effort():
    client, completions = _make_client(reasoning_effort="none")
    completions.set_result(_response(content="ok"))

    await client.complete([{"role": "user", "content": "x"}])

    assert completions.calls[-1]["extra_body"] == {
        "provider": {"data_collection": "deny", "zdr": True},
        "reasoning": {"effort": "none"},
    }


@pytest.mark.asyncio
async def test_complete_without_data_policy_sends_no_extra_body():
    client, completions = _make_client(data_collection=None, zdr=False)
    completions.set_result(_response(content="ok"))

    await client.complete([{"role": "user", "content": "x"}])

    assert "extra_body" not in completions.calls[-1]


@pytest.mark.asyncio
async def test_complete_no_usage_yields_none_metadata():
    client, completions = _make_client()
    completions.set_result(_response(content="ok", usage=None))
    await client.complete([{"role": "user", "content": "x"}])
    assert client.last_metadata.input_tokens is None
    assert client.last_metadata.estimated_cost_usd is None


@pytest.mark.asyncio
async def test_complete_retries_then_succeeds():
    client, completions = _make_client()
    completions.queue_errors(_conn_error())
    completions.set_result(_response(content="recovered"))

    with patch("endury.coach.llm.asyncio.sleep", new=AsyncMock()) as sleep:
        result = await client.complete([{"role": "user", "content": "x"}])

    assert result["content"] == "recovered"
    assert sleep.await_count == 1


@pytest.mark.asyncio
async def test_complete_retry_exhausted_raises():
    client, completions = _make_client(max_retries=1)
    completions.queue_errors(_conn_error(), _conn_error())

    with (
        patch("endury.coach.llm.asyncio.sleep", new=AsyncMock()),
        pytest.raises(APIConnectionError),
    ):
        await client.complete([{"role": "user", "content": "x"}])


@pytest.mark.asyncio
async def test_complete_non_retryable_raises_immediately():
    client, completions = _make_client()
    completions.queue_errors(_status_error(400))

    with pytest.raises(APIStatusError):
        await client.complete([{"role": "user", "content": "x"}])
    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_cost_read_from_sdk_usage_extra_field():
    """OpenRouter ships per-call cost as an extra field on the SDK usage model."""
    client, completions = _make_client()
    usage = CompletionUsage.model_validate(
        {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "cost": 0.0017}
    )
    completions.set_result(_response(content="ok", usage=usage))

    await client.complete([{"role": "user", "content": "x"}])

    assert client.last_metadata.estimated_cost_usd == 0.0017


# Retry classification


def test_should_retry_timeout():
    client, _ = _make_client()
    assert client._should_retry(APITimeoutError(request=None)) is True


def test_should_retry_status_codes():
    client, _ = _make_client()
    assert client._should_retry(_status_error(429)) is True
    assert client._should_retry(_status_error(400)) is False


def test_should_retry_other_error():
    client, _ = _make_client()
    assert client._should_retry(ValueError("nope")) is False


def test_retry_delay_grows():
    client, _ = _make_client(retry_backoff_ms=500)
    assert client._retry_delay_s(1) > client._retry_delay_s(0)


def test_metadata_as_dict_roundtrip():
    meta = LLMCallMetadata(model="m", latency_ms=1.0, input_tokens=2)
    assert meta.as_dict()["model"] == "m"
    assert meta.as_dict()["input_tokens"] == 2


# Constructor guards


def test_constructor_clamps_negative_retries():
    client, _ = _make_client(max_retries=-3, retry_backoff_ms=-1)
    assert client.max_retries == 0
    assert client.retry_backoff_ms == 0


def test_first_call_builds_the_real_sdk_without_client():
    client = OpenRouterClient(api_key="k", model="m")
    with patch("openai.AsyncOpenAI") as sdk_cls:
        assert client._client is None
        client._sdk()
        client._sdk()
    sdk_cls.assert_called_once()
    assert sdk_cls.call_args.kwargs["http_client"] is None
    assert sdk_cls.call_args.kwargs["default_headers"] == {
        "HTTP-Referer": "https://endury.app",
        "X-Title": "Endury",
    }


# The HTTP transport — identical natively and in Pyodide


@pytest.mark.asyncio
async def test_strip_stainless_drops_the_headers_the_browser_preflight_rejects():
    request = httpx.Request(
        "POST",
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "x-stainless-async": "async:asyncio",
            "x-stainless-read-timeout": "40",
            "x-stainless-lang": "python",
            "authorization": "Bearer k",
        },
    )
    await strip_stainless(request)
    for header in STRIPPED_REQUEST_HEADERS:
        assert header not in request.headers
    assert request.headers["x-stainless-lang"] == "python"
    assert request.headers["authorization"] == "Bearer k"


@pytest.mark.asyncio
async def test_strip_stainless_leaves_a_request_without_them_alone():
    request = httpx.Request("GET", "https://openrouter.ai/api/v1/models")
    await strip_stainless(request)
    assert "x-stainless-async" not in request.headers


@pytest.mark.asyncio
async def test_default_http_client_carries_the_hook():
    client = default_http_client()
    try:
        assert client.event_hooks["request"] == [strip_stainless]
    finally:
        await client.aclose()


# Factory


def test_create_llm_uses_config_model():
    config = CoachConfig(openrouter_api_key="or-key", model="some/model-x")
    client = create_llm(config)
    assert client.model == "some/model-x"


@pytest.mark.asyncio
async def test_create_llm_sends_through_the_hooked_transport_by_default():
    config = CoachConfig(openrouter_api_key="or-key")
    with patch("openai.AsyncOpenAI") as sdk_cls:
        create_llm(config)._sdk()
    transport = sdk_cls.call_args.kwargs["http_client"]
    try:
        assert isinstance(transport, httpx.AsyncClient)
        assert transport.event_hooks["request"] == [strip_stainless]
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_create_llm_takes_an_injected_transport():
    config = CoachConfig(openrouter_api_key="or-key")
    injected = httpx.AsyncClient()
    try:
        with patch("openai.AsyncOpenAI") as sdk_cls:
            create_llm(config, http_client=injected)._sdk()
        assert sdk_cls.call_args.kwargs["http_client"] is injected
    finally:
        await injected.aclose()


def test_create_llm_defaults_to_flash_lite():
    config = CoachConfig(openrouter_api_key="or-key")
    client = create_llm(config)
    assert client.model == "anthropic/claude-haiku-5.5"
    assert client.reasoning_effort == "low"


def test_factory_passes_transport_knobs():
    config = CoachConfig(
        openrouter_api_key="or-key",
        openrouter_timeout_s=12.0,
        openrouter_max_retries=7,
        openrouter_retry_backoff_ms=250,
    )
    client = create_llm(config)
    assert client.timeout_s == 12.0
    assert client.max_retries == 7
    assert client.retry_backoff_ms == 250
