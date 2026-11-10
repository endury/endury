"""Tests for the clock abstraction module."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from endury.common import clock


def test_today_returns_real_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COACHING_BUDDY_SIM_DATE", raising=False)
    result = clock.today()
    assert isinstance(result, date)
    assert result == date.today()


def test_today_returns_override_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-06-15")
    assert clock.today() == date(2026, 6, 15)


def test_now_returns_real_datetime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COACHING_BUDDY_SIM_DATE", raising=False)
    result = clock.now()
    assert isinstance(result, datetime)


def test_now_returns_override_with_default_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-06-15")
    monkeypatch.delenv("COACHING_BUDDY_SIM_TIME", raising=False)
    result = clock.now()
    assert result.date() == date(2026, 6, 15)
    assert result.hour == 12
    assert result.minute == 0


def test_now_respects_sim_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-06-15")
    monkeypatch.setenv("COACHING_BUDDY_SIM_TIME", "08:30:00")
    result = clock.now()
    assert result.date() == date(2026, 6, 15)
    assert result.hour == 8
    assert result.minute == 30


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, "night"),
        (4, "night"),
        (5, "early morning"),
        (8, "early morning"),
        (9, "morning"),
        (11, "morning"),
        (12, "midday"),
        (13, "midday"),
        (14, "afternoon"),
        (17, "afternoon"),
        (18, "evening"),
        (21, "evening"),
        (22, "night"),
        (23, "night"),
    ],
)
def test_daypart_covers_the_whole_clock(hour: int, expected: str) -> None:
    """Every hour of the day resolves to exactly one daypart label."""
    assert clock.daypart(hour) == expected


def test_daypart_names_every_pool_the_openers_carry() -> None:
    """select_opener indexes its pools by name — an unmapped one KeyErrors."""
    assert {clock.daypart(hour) for hour in range(24)} == {
        "night",
        "early morning",
        "morning",
        "midday",
        "afternoon",
        "evening",
    }
