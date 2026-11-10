"""Configuration for LLM-powered features and coach behavior."""

import logging
import os
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Values an operator ends up with by copying a template or a doc snippet
# instead of pasting a real key. Every one of them must behave like "no key
# at all": an instance that boots on a placeholder reports itself ready and
# then fails every coach turn with a raw 401.
_PLACEHOLDER_API_KEYS = frozenset(
    {
        "your_openrouter_key_here",
        "your_api_key_here",
        "your-key",
        "changeme",
        "replace_me",
        "replaceme",
        "todo",
        "none",
        "null",
    }
)

# Told to the operator whenever no usable OpenRouter key is found. Names the
# file the key belongs in (each instance is its own clone with its own
# repo-root .env), the per-instance spend cap, and the no-reuse rule.
_PROVISIONING_HINT = (
    "Set {env_var} in this instance's own .env (at that clone's repo root) "
    "to a per-instance OpenRouter key "
    "with its own monthly spend cap (https://openrouter.ai/settings/keys), "
    "then restart. Never reuse one key across instances."
)

# Told to the operator whenever no usable intervals.icu key is found.
_INTERVALS_PROVISIONING_HINT = (
    "Set {env_var} in this instance's own .env (at that clone's repo root) "
    "to your intervals.icu API key "
    "(https://intervals.icu/settings, Developer Settings), then restart."
)


def _usable_api_key(value: str | None) -> str | None:
    """Return a usable API key, or None when it is absent or a placeholder.

    Args:
        value: Raw key from a config field or an environment variable.

    Returns:
        The stripped key, or None when it is blank, wrapped in template
        markers (``<...>``), a known placeholder literal, or a redacted
        sample. An OpenRouter key is ``sk-or-v1-`` plus hex, so four
        consecutive ``x`` can only come from a redacted sample.
    """
    if value is None:
        return None
    key = value.strip()
    if not key:
        return None
    if key.startswith("<") and key.endswith(">"):
        return None
    folded = key.casefold()
    if folded in _PLACEHOLDER_API_KEYS or "xxxx" in folded:
        return None
    return key


class CoachConfig(BaseModel):
    """Configuration for the coaching agent."""

    # OpenRouter transport settings
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_s: float = 60.0
    openrouter_max_retries: int = 3
    openrouter_retry_backoff_ms: int = 500

    # Single model for every coach turn (OpenRouter slug).
    model: str = "anthropic/claude-haiku-5.5"

    # OpenRouter reasoning effort for the model above (none/low/medium/high/
    # xhigh/max); None omits the field and uses the model's own default.
    # "low" because the default model reasons unasked, at length: a full
    # activity debrief spent up to 3,961 reasoning tokens and ~45 s on it
    # (2026-10-15). Never "none": some models answer it with a 400
    # ("Reasoning is mandatory").
    reasoning_effort: str | None = "low"

    # OpenRouter provider routing, because a turn carries the athlete's health
    # data: "deny" avoids providers OpenRouter lists as collecting the prompt
    # (None leaves its default routing), and zdr keeps to endpoints it lists as
    # zero data retention. Its lists are its best knowledge, not a guarantee.
    openrouter_data_collection: str | None = "deny"
    openrouter_zdr: bool = True

    temperature: float = 0.4
    # Reasoning tokens count against max_tokens, and the default model
    # reasons unasked: a full activity debrief's last call measured up to
    # 4,784 tokens, 3,961 of them reasoning (2026-10-15). The timeout above
    # lets a call fill the cap at the ~186 tokens a second measured.
    max_tokens: int = 8000

    # API keys
    openrouter_api_key: str | None = None

    # intervals.icu transport settings
    intervals_base_url: str = "https://intervals.icu/api/v1"
    intervals_athlete_id: str = "0"
    intervals_api_key: str | None = None

    # Coach identity and behavior settings
    coach_title: str = "elite running and triathlon coach"
    verbose: bool = False

    # Conversation settings
    max_tool_rounds: int = 12
    # Prior conversation replayed to the model. It sits after the system
    # block's cache breakpoint, so every tool round of every turn re-sends it
    # uncached: 100 messages measured ~47k chars (~12k tokens) reaching back
    # nine days, 30 is ~11k and still covers the busiest day on record (24).
    # Durable facts live in the athlete-memory document, the scheduled-workouts
    # line, races and live intervals.icu data — the window only has to carry
    # the conversational thread. The page's own chat history is separate.
    max_message_history_messages: int = 30

    # Welcome line for a brand-new athlete (see coach/welcome.py).
    # intent: the coach is an AI model and says so at first contact.
    welcome_message: str = (
        "Hey. I'm your coach: an AI model, so I can be wrong, and bigger changes "
        "wait for your Apply. What's your name?"
    )

    # One opener pool per ``clock.daypart`` bucket (see coach/welcome.py).
    night_openers: list[str] = [
        "Still up, {name}?",
        "Late one, {name}.",
        "{name}! Burning the midnight oil?",
    ]

    early_morning_openers: list[str] = [
        "Morning, {name}.",
        "Early start, {name}?",
        "{name}! Up with the sun. I respect it.",
    ]

    morning_openers: list[str] = [
        "Morning, {name}.",
        "Hey {name}. How's the morning going?",
        "{name}! What's the plan today?",
    ]

    midday_openers: list[str] = [
        "Midday, {name}.",
        "Hey {name}. Mid-day check-in?",
        "{name}! How's the day treating you?",
    ]

    afternoon_openers: list[str] = [
        "Afternoon, {name}.",
        "Hey {name}. How's the day treating you?",
        "{name}! Good afternoon.",
    ]

    evening_openers: list[str] = [
        "Evening, {name}.",
        "Hey {name}. End-of-day check-in?",
        "{name}! How'd the day go?",
    ]

    def _get_credential(
        self, field: str, env_var: str, label: str, hint: str = _PROVISIONING_HINT
    ) -> str:
        """Retrieve a credential from a config field or environment variable.

        Args:
            field: Name of the config field (e.g. ``"openrouter_api_key"``).
            env_var: Environment variable name to fall back to.
            label: Human-readable label used in the error message.
            hint: Provisioning-instructions template (``{env_var}`` filled
                in), specific to where this credential comes from.

        Returns:
            The credential value.

        Raises:
            ValueError: When neither source holds a usable key -- both the
                absent and the placeholder case, which are reported
                differently because only one of them is an operator mistake.
        """
        field_value: str | None = getattr(self, field)
        env_value = os.getenv(env_var)
        for candidate in (field_value, env_value):
            usable = _usable_api_key(candidate)
            if usable is not None:
                return usable
        supplied = (field_value or "").strip() or (env_value or "").strip()
        state = (
            "is set to a placeholder value, not a real key"
            if supplied
            else "is not configured"
        )
        raise ValueError(f"{label} {state}. {hint.format(env_var=env_var)}")

    def get_openrouter_api_key(self) -> str:
        """Get OpenRouter API key from config or environment."""
        return self._get_credential(
            "openrouter_api_key", "OPENROUTER_API_KEY", "OpenRouter API key"
        )

    def get_intervals_api_key(self) -> str:
        """Get the intervals.icu API key from config or environment."""
        return self._get_credential(
            "intervals_api_key",
            "INTERVALS_API_KEY",
            "intervals.icu API key",
            hint=_INTERVALS_PROVISIONING_HINT,
        )


def get_coach_config() -> CoachConfig:
    """Return a fresh CoachConfig from environment values.

    OpenRouter is the coach's sole LLM transport.
    """
    from dotenv import load_dotenv

    load_dotenv()

    # A blank or placeholder key must behave like "no key at all" so a
    # copied-but-unedited .env lands in the graceful chat-disabled path
    # instead of booting a coach whose every turn fails with 401. The same
    # rule guards ``get_openrouter_api_key``, which reads the env var too.
    openrouter_key = _usable_api_key(os.environ.get("OPENROUTER_API_KEY"))

    # Same placeholder/blank guard as the OpenRouter key: a copied-but-unedited
    # .env must behave like "no key at all" for intervals.icu too.
    intervals_key = _usable_api_key(os.environ.get("INTERVALS_API_KEY"))

    kwargs: dict[str, Any] = {
        "openrouter_base_url": os.environ.get(
            "OPENROUTER_BASE_URL",
            "https://openrouter.ai/api/v1",
        ),
        "openrouter_api_key": openrouter_key,
        "intervals_base_url": os.environ.get(
            "INTERVALS_BASE_URL",
            "https://intervals.icu/api/v1",
        ),
        "intervals_athlete_id": os.environ.get("INTERVALS_ATHLETE_ID", "0"),
        "intervals_api_key": intervals_key,
    }

    if timeout_s := os.environ.get("OPENROUTER_TIMEOUT_S"):
        kwargs["openrouter_timeout_s"] = float(timeout_s)
    if max_retries := os.environ.get("OPENROUTER_MAX_RETRIES"):
        kwargs["openrouter_max_retries"] = int(max_retries)
    if backoff_ms := os.environ.get("OPENROUTER_RETRY_BACKOFF_MS"):
        kwargs["openrouter_retry_backoff_ms"] = int(backoff_ms)
    # Model id
    if model := os.environ.get("MODEL"):
        kwargs["model"] = model
    if reasoning_effort := os.environ.get("MODEL_REASONING_EFFORT"):
        kwargs["reasoning_effort"] = reasoning_effort

    return CoachConfig(**kwargs)
