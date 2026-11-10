"""Tests for CoachConfig loading and helpers."""

from unittest.mock import patch

import pytest

from endury.wiring.config import (
    CoachConfig,
    get_coach_config,
)


@pytest.fixture(autouse=True)
def no_dotenv():
    """Prevent reading real .env values in config tests.

    ``load_dotenv`` is imported lazily inside ``get_coach_config`` (not as a
    module-level name on ``endury.wiring.config``), so it is patched at its
    defining module instead.
    """
    with patch("dotenv.load_dotenv"):
        yield


def test_get_coach_config_returns_fresh_instance() -> None:
    """Each call returns a new CoachConfig object."""
    config1 = get_coach_config()
    config2 = get_coach_config()
    assert config1 is not config2


def test_load_dotenv_called_inside_get_coach_config() -> None:
    """load_dotenv is invoked lazily by get_coach_config."""
    with patch("dotenv.load_dotenv") as mock_load:
        get_coach_config()
        mock_load.assert_called_once()


def test_load_dotenv_is_not_a_module_level_name() -> None:
    """``load_dotenv`` must be imported lazily inside get_coach_config.

    Importing ``endury.wiring.config`` must not import ``dotenv`` as a
    module-level side effect, since the browser bundle never has a
    filesystem to read a ``.env`` from.
    """
    import endury.wiring.config as config_module

    assert not hasattr(config_module, "load_dotenv")


def test_openrouter_api_key_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An OpenRouter key in the environment populates the config."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    config = get_coach_config()
    assert config.openrouter_api_key == "or-test-key"


def test_no_keys_returns_default_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env keys set, get_coach_config still returns a valid config."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = get_coach_config()
    assert isinstance(config, CoachConfig)


def test_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MODEL env var overrides the default."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.setenv("MODEL", "some/model-x")

    config = get_coach_config()
    assert config.model == "some/model-x"


def test_default_model_is_flash_lite() -> None:
    """The coach defaults to a single Gemini 3.5 Flash Lite model."""
    assert CoachConfig().model == "anthropic/claude-haiku-5.5"


def test_default_history_window_is_thirty_messages() -> None:
    """The window sits after the cache breakpoint and is re-sent, uncached,
    on every tool round of every turn, so it must stay small while still
    covering the busiest day."""
    assert CoachConfig().max_message_history_messages == 30


def test_default_reasoning_effort_is_low() -> None:
    """The default model reasons at length unasked; "none" is refused by some."""
    assert CoachConfig().reasoning_effort == "low"


def test_completion_cap_holds_the_default_models_reasoning_and_reply() -> None:
    """The default model reasons unasked, and its reasoning counts against
    max_tokens: on the demo athlete (2026-10-15) a full debrief's last call
    used up to 4,784 completion tokens, 3,961 of them reasoning, at about 186
    tokens a second. A 2,000 cap cut the reply mid-word or left it empty, and
    a call allowed to fill the cap must not time out first."""
    longest_measured_tokens, tokens_per_s = 4784, 186
    config = CoachConfig()
    assert config.max_tokens >= 1.5 * longest_measured_tokens
    assert config.openrouter_timeout_s * tokens_per_s >= config.max_tokens


def test_reasoning_effort_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MODEL_REASONING_EFFORT env var overrides the default."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.setenv("MODEL_REASONING_EFFORT", "high")

    config = get_coach_config()
    assert config.reasoning_effort == "high"


def test_transport_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transport env vars are parsed into the matching config fields."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.setenv("OPENROUTER_TIMEOUT_S", "12.5")
    monkeypatch.setenv("OPENROUTER_MAX_RETRIES", "7")
    monkeypatch.setenv("OPENROUTER_RETRY_BACKOFF_MS", "250")

    config = get_coach_config()
    assert config.openrouter_timeout_s == 12.5
    assert config.openrouter_max_retries == 7
    assert config.openrouter_retry_backoff_ms == 250


def test_get_openrouter_api_key_from_config() -> None:
    """Returns key set directly on config."""
    config = CoachConfig(openrouter_api_key="or-direct")
    assert config.get_openrouter_api_key() == "or-direct"


def test_get_openrouter_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads OPENROUTER_API_KEY env var when field is unset."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-or-key")
    config = CoachConfig()
    assert config.get_openrouter_api_key() == "env-or-key"


def test_get_openrouter_api_key_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raises ValueError when no key is configured."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = CoachConfig()
    with pytest.raises(ValueError, match="OpenRouter API key is not configured"):
        config.get_openrouter_api_key()


# Per-instance key guard
#
# An instance whose env file holds a placeholder must land in the same
# chat-disabled state as one with no key at all. Booting on a placeholder
# reports coach_ready=true and then fails every turn with a raw 401.

PLACEHOLDERS = [
    "",
    "   ",
    "your_openrouter_key_here",
    "YOUR_OPENROUTER_KEY_HERE",
    "your_api_key_here",
    "changeme",
    "replace_me",
    "todo",
    "none",
    "null",
    "<your-key>",
    "sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
]

REAL_KEYS = [
    "sk-or-v1-0123456789abcdef0123456789abcdef",
    "  sk-or-v1-padded  ",
    "or-test-key",
]


@pytest.mark.parametrize("value", PLACEHOLDERS)
def test_placeholder_key_leaves_the_config_field_unset(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """get_coach_config() normalizes every placeholder form to None."""
    monkeypatch.setenv("OPENROUTER_API_KEY", value)
    assert get_coach_config().openrouter_api_key is None


@pytest.mark.parametrize("value", PLACEHOLDERS)
def test_placeholder_key_is_rejected_by_the_accessor_too(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The accessor re-reads the env var itself, so it must apply the same
    placeholder guard as get_coach_config() — the path app startup calls.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", value)
    with pytest.raises(ValueError, match=r"placeholder|not configured"):
        get_coach_config().get_openrouter_api_key()


@pytest.mark.parametrize("value", REAL_KEYS)
def test_real_looking_keys_are_not_mistaken_for_placeholders(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The guard must never swallow a working key."""
    monkeypatch.setenv("OPENROUTER_API_KEY", value)
    assert get_coach_config().get_openrouter_api_key() == value.strip()


def test_placeholder_on_the_config_field_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A placeholder passed straight to CoachConfig is caught as well."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    config = CoachConfig(openrouter_api_key="<your-key>")
    with pytest.raises(ValueError, match="is set to a placeholder value"):
        config.get_openrouter_api_key()


def test_missing_and_placeholder_are_reported_differently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only one of the two is an operator mistake; the message says which."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OpenRouter") as missing:
        CoachConfig().get_openrouter_api_key()
    monkeypatch.setenv("OPENROUTER_API_KEY", "changeme")
    with pytest.raises(ValueError, match="OpenRouter") as placeholder:
        CoachConfig().get_openrouter_api_key()

    assert "is not configured" in str(missing.value)
    assert "is set to a placeholder value" in str(placeholder.value)


def test_key_error_message_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator must learn where the key goes and that it needs a cap."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OpenRouter") as excinfo:
        CoachConfig().get_openrouter_api_key()

    message = str(excinfo.value)
    assert "OPENROUTER_API_KEY" in message
    assert "this instance's own .env" in message
    assert "monthly spend cap" in message
    assert "Never reuse one key across instances" in message


def test_key_error_message_never_echoes_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misfiring guard must not spill a real key into the startup log."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-xxxxsecretsuffix")
    with pytest.raises(ValueError, match="OpenRouter") as excinfo:
        CoachConfig().get_openrouter_api_key()

    assert "secretsuffix" not in str(excinfo.value)


# intervals.icu transport settings


def test_intervals_defaults_without_env() -> None:
    """athlete_id and base_url default even with no env vars set."""
    config = CoachConfig()
    assert config.intervals_athlete_id == "0"
    assert config.intervals_base_url == "https://intervals.icu/api/v1"
    assert config.intervals_api_key is None


def test_get_coach_config_reads_intervals_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERVALS_API_KEY", "ic-test-key")
    monkeypatch.setenv("INTERVALS_ATHLETE_ID", "12345")
    monkeypatch.setenv("INTERVALS_BASE_URL", "https://example.test/api/v1")

    config = get_coach_config()
    assert config.intervals_api_key == "ic-test-key"
    assert config.intervals_athlete_id == "12345"
    assert config.intervals_base_url == "https://example.test/api/v1"


def test_get_intervals_api_key_from_config() -> None:
    config = CoachConfig(intervals_api_key="ic-direct")
    assert config.get_intervals_api_key() == "ic-direct"


def test_get_intervals_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERVALS_API_KEY", "ic-env-key")
    config = CoachConfig()
    assert config.get_intervals_api_key() == "ic-env-key"


def test_get_intervals_api_key_raises_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    config = CoachConfig()
    with pytest.raises(ValueError, match=r"intervals\.icu API key is not configured"):
        config.get_intervals_api_key()


def test_intervals_key_error_message_does_not_say_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provisioning hint must name the credential it's actually about."""
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    with pytest.raises(ValueError, match="INTERVALS_API_KEY") as excinfo:
        CoachConfig().get_intervals_api_key()

    message = str(excinfo.value)
    assert "OpenRouter" not in message
    assert "monthly spend cap" not in message
    assert "intervals.icu" in message


def test_intervals_placeholder_key_leaves_the_config_field_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same placeholder guard as OpenRouter applies to the intervals key."""
    monkeypatch.setenv("INTERVALS_API_KEY", "changeme")
    assert get_coach_config().intervals_api_key is None


def test_intervals_placeholder_key_is_rejected_by_the_accessor_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTERVALS_API_KEY", "changeme")
    with pytest.raises(ValueError, match=r"placeholder|not configured"):
        get_coach_config().get_intervals_api_key()
