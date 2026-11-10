"""OpenRouter LLM client: one-shot chat completions with retries.

A single async client speaks the OpenAI chat-completions wire format directly —
messages are plain dicts (``{"role", "content", ...}``) throughout the coach,
so no message-object translation layer is needed. The client retries
transient transport errors with deterministic backoff and records per-call
metadata. Its HTTP transport strips the SDK's ``x-stainless-*`` request
headers OpenRouter's browser CORS preflight rejects, so the same client runs
natively and in Pyodide. The SDK itself is imported with the first call, not
with the coach: a booted app that never chats never loads it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from endury.wiring.config import CoachConfig

logger = logging.getLogger(__name__)

#: SDK telemetry headers that fail OpenRouter's CORS preflight in a browser.
#: Stripped everywhere, so native and Pyodide requests are byte-identical.
STRIPPED_REQUEST_HEADERS = ("x-stainless-async", "x-stainless-read-timeout")

#: OpenRouter's optional app attribution (its rankings page): the project
#: home, not the hosting origin, so a self-hosted copy credits Endury too.
APP_ATTRIBUTION_HEADERS = {"HTTP-Referer": "https://endury.app", "X-Title": "Endury"}


async def strip_stainless(request: httpx.Request) -> None:
    """Drop :data:`STRIPPED_REQUEST_HEADERS` from an outgoing request.

    Args:
        request: The request httpx is about to send (an ``event_hooks``
            ``request`` hook).
    """
    for header in STRIPPED_REQUEST_HEADERS:
        request.headers.pop(header, None)


def default_http_client() -> httpx.AsyncClient:
    """Return the transport ``create_llm`` uses: httpx with the header hook."""
    return httpx.AsyncClient(event_hooks={"request": [strip_stainless]})


@dataclass
class LLMCallMetadata:
    """Per-call metadata captured from OpenRouter responses."""

    model: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return JSON-serializable metadata."""
        return asdict(self)


def _message_to_dict(msg: Any) -> dict[str, Any]:
    """Convert an OpenAI response message object into a canonical assistant dict.

    Args:
        msg: The ``response.choices[0].message`` object from the OpenAI SDK.

    Returns:
        An assistant message dict in OpenAI wire format, with ``tool_calls``
        included only when the model requested tool calls.
    """
    result: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        result["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "",
                },
            }
            for tc in tool_calls
        ]
    return result


class OpenRouterClient:
    """Async OpenRouter chat client with retries and metadata."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        temperature: float = 0.4,
        max_tokens: int = 8000,
        timeout_s: float = 60.0,
        max_retries: int = 3,
        retry_backoff_ms: int = 500,
        reasoning_effort: str | None = None,
        data_collection: str | None = "deny",
        zdr: bool = True,
        client: Any = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.data_collection = data_collection
        self.zdr = zdr
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)
        self.retry_backoff_ms = max(0, retry_backoff_ms)
        self.last_metadata: LLMCallMetadata | None = None
        self._client = client
        self._sdk_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
            "timeout": timeout_s,
            "http_client": http_client,
            "default_headers": APP_ATTRIBUTION_HEADERS,
        }

    def _sdk(self) -> Any:
        """Return the OpenAI SDK client, built — and imported — on the first call.

        In the browser the SDK costs about a second of boot and its share of
        memory, and a booted app that never chats never needs it.
        """
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(**self._sdk_kwargs)
        return self._client

    # Retry + metadata helpers

    def _should_retry(self, exc: Exception) -> bool:
        """Return whether a transport error is worth retrying."""
        from openai import APIConnectionError, APIStatusError, APITimeoutError

        if isinstance(exc, (APIConnectionError, APITimeoutError)):
            return True
        if isinstance(exc, APIStatusError):
            return exc.status_code in {429, 500, 502, 503, 504}
        return False

    def _retry_delay_s(self, attempt: int) -> float:
        """Return deterministic backoff delay (seconds) for a retry attempt."""
        base = (float(self.retry_backoff_ms) / 1000.0) * float(2**attempt)
        jitter = float(max(0.05, base * 0.1))
        return float(base + jitter)

    def _metadata_from_usage(
        self,
        usage: Any,
        latency_ms: float,
        model: str,
        request_id: str | None,
    ) -> LLMCallMetadata:
        """Build call metadata from an OpenAI ``usage`` object (may be None).

        ``cost`` is OpenRouter's per-call dollar amount — an extra field on the
        SDK's usage model.
        """
        return LLMCallMetadata(
            model=model,
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
            estimated_cost_usd=getattr(usage, "cost", None) if usage else None,
            request_id=request_id,
        )

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
    ) -> dict[str, Any]:
        """Assemble the kwargs for a chat-completions call."""
        call_kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            call_kwargs["tools"] = tools
        extra_body: dict[str, Any] = {}
        provider: dict[str, Any] = {}
        if self.data_collection:
            # "deny" asks OpenRouter to avoid providers it lists as collecting
            # the prompt; its listing is its best knowledge, not a guarantee.
            provider["data_collection"] = self.data_collection
        if self.zdr:
            # Only endpoints OpenRouter lists as zero data retention. A model
            # without one answers 404, which _no_endpoint_message words.
            provider["zdr"] = True
        if provider:
            extra_body["provider"] = provider
        if self.reasoning_effort:
            extra_body["reasoning"] = {"effort": self.reasoning_effort}
        if extra_body:
            call_kwargs["extra_body"] = extra_body
        return call_kwargs

    # Public API

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Return one assistant message dict for the given conversation."""
        call_kwargs = self._build_kwargs(messages, tools, model)
        resolved_model = str(call_kwargs["model"])
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            try:
                response = await self._sdk().chat.completions.create(**call_kwargs)
                latency_ms = (time.perf_counter() - started) * 1000.0
                logger.debug("served by %s", getattr(response, "provider", None))
                self.last_metadata = self._metadata_from_usage(
                    getattr(response, "usage", None),
                    latency_ms,
                    resolved_model,
                    getattr(response, "id", None),
                )
                return _message_to_dict(response.choices[0].message)
            except Exception as exc:
                if getattr(exc, "status_code", None) == 404 and self.zdr:
                    raise RuntimeError(_no_endpoint_message(resolved_model)) from exc
                if attempt >= self.max_retries or not self._should_retry(exc):
                    raise
                await asyncio.sleep(self._retry_delay_s(attempt))
        raise RuntimeError(  # pragma: no cover — loop always returns or raises
            "OpenRouter complete retry loop terminated unexpectedly."
        )


def _no_endpoint_message(model: str) -> str:
    """Say why a model with no zero-retention endpoint cannot chat."""
    return (
        f"OpenRouter lists no zero-data-retention service for {model}. "
        "Pick another AI model in Settings, or leave it empty for Endury's default."
    )


# Factories


def create_llm(
    config: CoachConfig, http_client: httpx.AsyncClient | None = None
) -> OpenRouterClient:
    """Create the OpenRouter client from ``CoachConfig`` (one model, all turns).

    Args:
        config: Model and transport settings.
        http_client: The httpx transport to send through; defaults to
            :func:`default_http_client`, which carries the header hook.
    """
    return OpenRouterClient(
        api_key=config.get_openrouter_api_key(),
        model=config.model,
        base_url=config.openrouter_base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout_s=config.openrouter_timeout_s,
        max_retries=config.openrouter_max_retries,
        retry_backoff_ms=config.openrouter_retry_backoff_ms,
        reasoning_effort=config.reasoning_effort,
        data_collection=config.openrouter_data_collection,
        zdr=config.openrouter_zdr,
        http_client=http_client or default_http_client(),
    )


__all__ = [
    "APP_ATTRIBUTION_HEADERS",
    "STRIPPED_REQUEST_HEADERS",
    "LLMCallMetadata",
    "OpenRouterClient",
    "create_llm",
    "default_http_client",
    "strip_stainless",
]
