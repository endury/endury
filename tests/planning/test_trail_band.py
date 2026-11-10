"""Tests for the trail km-effort table, incl. parity with the frontend wizard."""

from __future__ import annotations

import re
from pathlib import Path

from endury.planner.foundations.types import GoalType
from endury.planning.trail_band import (
    TRAIL_RACE_GOALS,
    band_thresholds_prose,
    km_effort,
    trail_goal_for,
)

_SHARED_JS = Path(__file__).resolve().parents[2] / "web/shells/shared.js"


class TestKmEffort:
    def test_adds_climb_at_100m_per_km(self) -> None:
        assert km_effort(30, 2000) == 50


class TestTrailGoalFor:
    def test_under_50_is_20k(self) -> None:
        assert trail_goal_for(49, 0) == GoalType.TRAIL_20K

    def test_50_is_50k(self) -> None:
        assert trail_goal_for(50, 0) == GoalType.TRAIL_50K

    def test_99_is_50k(self) -> None:
        assert trail_goal_for(99, 0) == GoalType.TRAIL_50K

    def test_100_is_100k(self) -> None:
        assert trail_goal_for(100, 0) == GoalType.TRAIL_100K

    def test_169_is_100k(self) -> None:
        assert trail_goal_for(169, 0) == GoalType.TRAIL_100K

    def test_170_has_no_plan(self) -> None:
        assert trail_goal_for(170, 0) is None
        assert trail_goal_for(100, 7000) is None

    def test_climb_pushes_a_short_distance_into_a_longer_band(self) -> None:
        # 30 km with 4000 m of climb: 30 + 40 = 70 km-effort -> 50K, not 20K.
        assert trail_goal_for(30, 4000) == GoalType.TRAIL_50K


class TestTrailRaceGoals:
    def test_covers_exactly_the_three_race_band_goals(self) -> None:
        assert {"trail_20k", "trail_50k", "trail_100k"} == TRAIL_RACE_GOALS


class TestBandThresholdsProse:
    def test_matches_the_athlete_facing_wording(self) -> None:
        assert band_thresholds_prose() == (
            "<50 Trail 20K, 50-99 Trail 50K, 100-169 Trail 100K, 170+ no plan"
        )


def _js_thresholds() -> list[tuple[int, str | None]]:
    """Parse trailBandForEffort's `if (kmEffort >= N) return 'X';` (or null) lines."""
    text = _SHARED_JS.read_text()
    return [
        (int(bound), goal or None)
        for bound, goal in re.findall(
            r"if \(kmEffort >= (\d+)\) return (?:'(\w+)'|null);", text
        )
    ]


def _js_band_for(effort: float, thresholds: list[tuple[int, str | None]]) -> str | None:
    """Re-run the JS cascade in Python, over the parsed thresholds."""
    for bound, goal in thresholds:
        if effort >= bound:
            return goal
    return "trail_20k"


class TestPythonJsParity:
    def test_the_frontend_wizard_still_has_exactly_these_three_cutoffs(self) -> None:
        assert _js_thresholds() == [
            (170, None),
            (100, "trail_100k"),
            (50, "trail_50k"),
        ]

    def test_python_table_agrees_with_the_frontend_wizard_everywhere(self) -> None:
        thresholds = _js_thresholds()
        for effort in (0, 10, 49.9, 50, 75, 99.9, 100, 150, 169.9, 170, 500):
            goal = trail_goal_for(effort, 0)
            assert (goal.value if goal else None) == _js_band_for(effort, thresholds)
