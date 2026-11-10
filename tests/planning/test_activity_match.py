"""Tests for recognising which planned session a recorded activity was."""

from __future__ import annotations

import pytest

from endury.planning.activity_match import carries_title


@pytest.mark.parametrize(
    ("name", "title"),
    [
        ("Hillside - Long Trail Run", "Long Trail Run"),
        ("Lakeside - Optional: Aerobic Tempo Run", "Optional: Aerobic Tempo Run"),
        ("Lakeside - Aerobic Tempo Run", "Optional: Aerobic Tempo Run"),
        ("long trail run", "Long Trail Run"),
    ],
)
def test_a_watch_workout_carries_its_title(name: str, title: str) -> None:
    assert carries_title(name, title)


@pytest.mark.parametrize(
    ("name", "title"),
    [
        ("Riverside Running", "Easy Run"),
        ("Hillside - Long Trail Run", "Easy Run"),
        ("Easy Run + Strides", "Easy Run"),
        (None, "Easy Run"),
        ("Hillside - Easy Run", None),
    ],
)
def test_a_free_run_carries_no_title(name: str | None, title: str | None) -> None:
    assert not carries_title(name, title)
