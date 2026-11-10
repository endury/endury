"""Post-turn response extraction and profile side-effect helpers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from endury.coach.messages import find_last_by_role, has_tool_calls
from endury.coach.reply.postprocess import clean_coach_response

logger = logging.getLogger(__name__)

Message = dict[str, Any]

_NAME_PROMPT_PHRASES = (
    "what's your name",
    "what should i call you",
    "your name",
    "call you",
)

# Conversational lead-ins that wrap a name ("I'm Alex") — longest first so the
# more specific prefix wins.
_NAME_LEAD_INS = (
    "my name is",
    "my name's",
    "the name's",
    "name is",
    "name's",
    "call me",
    "this is",
    "i am",
    "it is",
    "it's",
    "i'm",
    "its",
    "im",
)

# Replies that answer a name question without giving a name.
_NAME_REFUSALS = frozenset(
    {
        "no",
        "no thanks",
        "no thank you",
        "nope",
        "nah",
        "not now",
        "not telling you",
        "later",
        "maybe later",
        "why",
        "who cares",
        "none",
        "nothing",
        "skip",
        "pass",
        "rather not",
        "rather not say",
        "prefer not",
        "prefer not to say",
        "anonymous",
        "yes",
        "ok",
        "okay",
        "sure",
        "what",
        "huh",
        "idk",
        "dunno",
    }
)

_NAME_TRIM_CHARS = " \t.,!;:\"'`-?"


def _is_name_refusal(text: str) -> bool:
    """Return whether *text* is a refusal rather than a name."""
    return text.strip(_NAME_TRIM_CHARS).lower() in _NAME_REFUSALS


def _extract_name(text: str) -> str | None:
    """Extract a plausible athlete name from a free-text reply.

    Strips conversational lead-ins ("call me Alex") and rejects refusals and
    anything that does not look like a name (digits, URLs, whole sentences).

    Args:
        text: The athlete's raw reply to a name question.

    Returns:
        The name to persist, or None when nothing name-like was offered.
    """
    candidate = text.strip()
    if _is_name_refusal(candidate):
        return None

    lowered = candidate.lower()
    for lead_in in _NAME_LEAD_INS:
        if lowered.startswith(lead_in) and (
            len(lowered) == len(lead_in) or not lowered[len(lead_in)].isalnum()
        ):
            candidate = candidate[len(lead_in) :]
            break

    candidate = candidate.strip(_NAME_TRIM_CHARS)
    if not candidate or _is_name_refusal(candidate):
        return None
    if not (1 <= len(candidate) <= 40) or len(candidate.split()) > 3:
        return None
    if any(char.isdigit() for char in candidate):
        return None
    lowered = candidate.lower()
    if "http" in lowered or "www." in lowered or "@" in candidate:
        return None
    if not any(char.isalpha() for char in candidate):
        return None
    return candidate


def _asked_for_name(messages: list[Message], last_ai: Message | None) -> bool:
    """Return whether the recent coach conversation asked for the athlete's name."""
    if last_ai is not None:
        ai_text = str(last_ai.get("content", "")).lower()
        if any(phrase in ai_text for phrase in _NAME_PROMPT_PHRASES):
            return True

    for msg in reversed(messages):
        if (
            msg.get("role") == "assistant"
            and not has_tool_calls(msg)
            and any(
                phrase in str(msg.get("content", "")).lower()
                for phrase in _NAME_PROMPT_PHRASES
            )
        ):
            return True
    return False


def _tool_messages(messages: list[Message]) -> list[Message]:
    """Return all tool messages in one turn."""
    return [msg for msg in messages if msg.get("role") == "tool"]


class SessionFinalizer:
    """Apply post-turn response cleanup and side effects."""

    def __init__(self, runtime: CoachRuntime) -> None:
        self._runtime = runtime

    @staticmethod
    def extract_response(messages: list[Message]) -> str:
        """Extract final text content from the last substantive assistant message."""
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            if has_tool_calls(msg):
                continue
            raw = str(msg.get("content", ""))
            if raw and raw.strip():
                return clean_coach_response(raw)
        return ""

    def _sync_profile_if_needed(self, tool_messages: list[Message]) -> None:
        """Refresh athlete profile when a profile tool updated it."""
        if not any(
            msg.get("name") == "update_athlete_profile" for msg in tool_messages
        ):
            return
        try:
            self._runtime.refresh_athlete_profile()
        except Exception as exc:
            logger.warning("Could not sync athlete profile after update: %s", exc)

    def _auto_save_name(
        self,
        messages: list[Message],
        last_human: Message | None,
        last_ai: Message | None,
    ) -> None:
        """Persist the athlete name when the conversation clearly provided it."""
        current_profile = self._runtime.athlete_profile
        if current_profile is not None and current_profile.name:
            return
        if last_human is None or not _asked_for_name(messages, last_ai):
            return

        human_text = str(last_human.get("content", "")).strip()
        if not (1 <= len(human_text) <= 50):
            return
        if "?" in human_text or "http" in human_text:
            return

        name = _extract_name(human_text)
        if name is None:
            logger.info("_auto_save_name: reply is not a name — leaving profile blank")
            return

        try:
            self._runtime.athlete_repo.update({"name": name})
            self._runtime.refresh_athlete_profile()
            logger.info(
                "_auto_save_name: saved name '%s' from user message",
                name,
            )
        except Exception as exc:
            logger.warning("Could not auto-save name: %s", exc)

    def finalize(self, messages: list[Message]) -> None:
        """Apply the turn's profile side effects (name capture, profile sync).

        Durable athlete context is not inferred here: the coach owns it
        explicitly through ``update_memory``.

        Args:
            messages: Every message of the finished turn, history included.
        """
        try:
            last_human = find_last_by_role(messages, "user")
            last_ai = find_last_by_role(messages, "assistant", exclude_tool_calls=True)

            self._sync_profile_if_needed(_tool_messages(messages))
            self._auto_save_name(messages, last_human, last_ai)
        except Exception as exc:
            logger.warning("Could not apply post-turn profile updates: %s", exc)


if TYPE_CHECKING:
    from endury.coach.runtime import CoachRuntime
