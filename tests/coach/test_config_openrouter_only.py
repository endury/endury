"""Tests for OpenRouter-only config behavior."""

from endury.wiring.config import CoachConfig, get_coach_config


def test_default_model_is_flash_lite() -> None:
    assert CoachConfig().model == "anthropic/claude-haiku-5.5"


def test_get_coach_config_reads_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("MODEL", "some/model-x")
    cfg = get_coach_config()
    assert cfg.model == "some/model-x"
