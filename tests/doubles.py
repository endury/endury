"""Reusable test doubles for Endury tests.

The coach speaks OpenAI chat-completions dicts, so these fakes stand in for
:class:`endury.coach.llm.OpenRouterClient` by implementing ``complete``
over queued assistant-message dicts. ``FakeIntervals`` (an
in-memory intervals.icu behind a real ``IntervalsClient``) lives in
``endury.intervals.fake`` so the browser E2E harness and the demo athlete
can use it too; it is re-exported here.
"""

from __future__ import annotations

import json
from typing import Any

from endury.domain.models.athlete import AthleteProfile
from endury.intervals.fake import FakeIntervals as FakeIntervals

Message = dict[str, Any]


def assistant_message(
    content: str = "", tool_calls: list[dict[str, Any]] | None = None
) -> Message:
    """Build an assistant message dict, optionally carrying tool calls."""
    msg: Message = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def tool_call(
    call_id: str, name: str, arguments: dict[str, Any] | str
) -> dict[str, Any]:
    """Build an OpenAI-wire tool_call entry (arguments serialized to JSON)."""
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


class FakeLLM:
    """Fake OpenRouter client returning queued assistant-message dicts.

    Args:
        responses: Ordered assistant dicts or plain strings to return. If None,
            a single generic greeting is queued.
    """

    def __init__(self, responses: list[Any] | None = None) -> None:
        source = (
            responses
            if responses is not None
            else [{"role": "assistant", "content": "Hello!"}]
        )
        self._responses = [self._normalize(r) for r in source]
        self.last_metadata: Any = None

    @staticmethod
    def _normalize(response: Any) -> Message:
        if isinstance(response, str):
            return {"role": "assistant", "content": response}
        return dict(response)

    def _next(self) -> Message:
        if not self._responses:
            return {"role": "assistant", "content": "Default response"}
        return self._responses.pop(0)

    async def complete(self, messages, tools=None, model=None) -> Message:
        """Return the next queued response."""
        return self._next()


class CapturingLLM(FakeLLM):
    """FakeLLM that records the messages and tools passed to each call."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        super().__init__(responses)
        self.invocations: list[list[Message]] = []

    async def complete(self, messages, tools=None, model=None) -> Message:
        self.invocations.append(list(messages))
        return self._next()

    @property
    def last_system_prompt(self) -> str | None:
        """Return the system prompt from the most recent invocation."""
        if not self.invocations:
            return None
        for msg in self.invocations[-1]:
            if msg.get("role") == "system":
                content = msg["content"]
                if isinstance(content, str):
                    return content
                return "".join(part["text"] for part in content)
        return None

    @property
    def last_messages(self) -> list[Message] | None:
        """Return the messages from the most recent invocation."""
        return self.invocations[-1] if self.invocations else None


class _FakeAgentRef:
    """Minimal stand-in for the prompt-building runtime context."""

    def __init__(self) -> None:
        self.athlete_profile = AthleteProfile(
            name="Alex",
            experience_level="intermediate",
            primary_sport="running",
        )
        self.recent_health_summary = None
        self.active_plan_summary = None
        self.macro_plan_summary = None
