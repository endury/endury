"""Tests for build_projections (daily CTL/TSB forecast arrays)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from endury.planner.foundations.request import AthleteState, SportState
from endury.planner.foundations.types import ActivityKey, GoalType, SportType
from endury.planner.macro.session_pricing import session_tss_per_hour
from endury.planner.pipeline import seed_tss_per_hour
from endury.planning.projections import build_projections

_START = date(2026, 1, 5)
_CTL_DECAY = math.exp(-1.0 / 42.0)


def _session(day: date, sport: str, tss: float) -> dict:
    return {
        "date": day.isoformat(),
        "sport": sport,
        "achieved_tss": tss,
        "week_index": 0,
        "activity_key": "run_easy",
        "duration_minutes": 60.0,
    }


def _athlete(ctl_weekly: float = 350.0) -> AthleteState:
    return AthleteState(
        sports={
            SportType.RUNNING: SportState(
                recent_weekly_hours=7.0, ctl_0=ctl_weekly, atl_0=ctl_weekly
            ),
        }
    )


class TestBuildProjections:
    def test_array_length_covers_plan_to_race_day(self) -> None:
        result = build_projections(
            sessions=[],
            athlete=_athlete(),
            goal_type=GoalType.RUNNING_MARATHON,
            plan_start=_START,
            goal_date=date(2026, 1, 18),
        )
        assert len(result["aggregate_ctl"]) == 14
        assert len(result["ctl_by_sport"]["running"]) == 14
        assert result["goal_type"] == "running_marathon"

    def test_seeded_from_weekly_state_in_daily_space(self) -> None:
        """Weekly ctl_0=350 seeds the daily model at 50 (the ÷7 bridge)."""
        result = build_projections(
            sessions=[],
            athlete=_athlete(ctl_weekly=350.0),
            goal_type=GoalType.RUNNING_MARATHON,
            plan_start=_START,
            goal_date=_START,
        )
        expected_day_0 = 50.0 * _CTL_DECAY  # rest day: pure decay
        assert result["ctl_by_sport"]["running"][0] == pytest.approx(
            expected_day_0, abs=0.01
        )

    def test_training_raises_ctl_and_depresses_tsb(self) -> None:
        sessions = [_session(date(2026, 1, 5 + i), "running", 100.0) for i in range(14)]
        result = build_projections(
            sessions=sessions,
            athlete=_athlete(ctl_weekly=350.0),
            goal_type=GoalType.RUNNING_MARATHON,
            plan_start=_START,
            goal_date=date(2026, 1, 18),
        )
        ctl = result["ctl_by_sport"]["running"]
        tsb = result["tsb_by_sport"]["running"]
        assert ctl[-1] > ctl[0]
        assert tsb[-1] < 0  # heavy block → fatigued

    def test_aggregate_is_sum_of_sports(self) -> None:
        athlete = AthleteState(
            sports={
                SportType.RUNNING: SportState(recent_weekly_hours=6.0, ctl_0=300.0),
                SportType.CYCLING: SportState(recent_weekly_hours=8.0, ctl_0=440.0),
            }
        )
        sessions = [
            _session(_START, "running", 80.0),
            _session(_START, "cycling", 120.0),
        ]
        result = build_projections(
            sessions=sessions,
            athlete=athlete,
            goal_type=GoalType.HALF_IRONMAN,
            plan_start=_START,
            goal_date=date(2026, 1, 11),
        )
        for i, value in enumerate(result["aggregate_ctl"]):
            assert value == pytest.approx(
                result["ctl_by_sport"]["running"][i]
                + result["ctl_by_sport"]["cycling"][i],
                abs=0.02,
            )

    def test_hours_derived_seed_when_ctl_missing(self) -> None:
        """No measured ctl_0 → seed from hours x TSS/h (pipeline parity)."""
        athlete = AthleteState(
            sports={SportType.RUNNING: SportState(recent_weekly_hours=7.0)}
        )
        result = build_projections(
            sessions=[],
            athlete=athlete,
            goal_type=GoalType.RUNNING_10K,
            plan_start=_START,
            goal_date=_START,
        )
        # No opening-week sessions: seeded off the TID bridge fallback.
        expected = (
            7.0 * seed_tss_per_hour(GoalType.RUNNING_10K, ()) / 7.0
        ) * _CTL_DECAY
        assert result["ctl_by_sport"]["running"][0] == pytest.approx(expected, abs=0.01)

    def test_seed_speaks_the_opening_week_activity_price(self) -> None:
        """A week-0 session seeds CTL at its own activity's TSS/h."""
        athlete = AthleteState(
            sports={SportType.RUNNING: SportState(recent_weekly_hours=7.0)}
        )
        tempo = {
            **_session(_START + timedelta(days=1), "running", 0.0),
            "activity_key": "run_tempo",
            "duration_minutes": 55.0,
            "duration_target_minutes": 50.0,
        }
        result = build_projections(
            sessions=[tempo],
            athlete=athlete,
            goal_type=GoalType.RUNNING_10K,
            plan_start=_START,
            goal_date=_START,
        )
        price = session_tss_per_hour(ActivityKey.RUN_TEMPO, 50.0 / 60.0)
        assert price != pytest.approx(seed_tss_per_hour(GoalType.RUNNING_10K, ()))
        expected = (7.0 * price / 7.0) * _CTL_DECAY
        assert result["ctl_by_sport"]["running"][0] == pytest.approx(expected, abs=0.01)
