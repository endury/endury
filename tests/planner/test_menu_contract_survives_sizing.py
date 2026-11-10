"""A squeezed trail week keeps its climbing session (session_generator V7).

A menu's ``must_contain`` is a menu-level contract checked when the menu
loads (R-SESS-018), not a promise about every emitted week: the race-sim
week swaps its downhill repeats for uphill ones, and the 100K
back-to-backs come and go by design. The per-week promise is R-SESS-033: a
climbing session is a key session, so every BUILD/PEAK load week and
every taper week of a trail race plan holds one whenever it fits its
share of the week. Capacity sizing shrinks and drops the easy runs first.

The first case is a real athlete's: a competitive trail 50K five weeks
out, joined at week 8 of 12, sized from 3.5 h/week of recent running.
Before key sessions the plan had no climbing session in any week, for a
race with 1,300 m of gain.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from endury.planner.foundations.request import AthleteState, PlanRequest, SportState
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    PhaseType,
    RaceGoal,
    SportType,
    WeekMode,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan

#: Sessions that put climbing or descending into a trail week.
_CLIMBING = {"run_vert_hike", "run_hill_repeats", "run_downhill_repeats"}

_TRAIL_GOALS = (
    GoalType.TRAIL_20K,
    GoalType.TRAIL_50K,
    GoalType.TRAIL_100K,
)

#: The 8-week basic 20K peaks near 3 h/week: its hill session (a 50-min
#: card) cannot fit a quarter of those weeks, so it drops by design.
_TOO_SMALL_TO_CLIMB = {(GoalType.TRAIL_20K, AmbitionLevel.BASIC)}


def _trail_50k_short_notice(recent_hours: float):
    athlete = AthleteState(
        sports={
            SportType.RUNNING: SportState(
                recent_weekly_hours=recent_hours,
                longest_recent_min={ActivityFamily.LONG: 230},
                threshold_pace_sec_per_km=250,
            )
        }
    )
    return generate_plan(
        PlanRequest(
            goals=[RaceGoal(GoalType.TRAIL_50K, date(2026, 10, 24), "A")],
            training_start=date(2026, 9, 21),
            ambition=AmbitionLevel.COMPETITIVE,
            athlete=athlete,
            prior_training_weeks=7,
        )
    )


def _weeks(result):
    """Yield each week's (phase, mode, emitted activity keys)."""
    for week in result.phase_plan.weeks:
        keys = {s.activity_key.value for s in result.sessions if s.week == week.week}
        yield week.phase, week.mode.value, keys


@pytest.mark.parametrize("recent_hours", [3.53, 6.0])
def test_every_training_week_of_a_trail_plan_climbs(recent_hours):
    # Race week is exempt: it sheds everything but easy running and strides.
    result = _trail_50k_short_notice(recent_hours)
    without = [
        (phase.value, mode)
        for phase, mode, keys in _weeks(result)
        if mode in ("load", "taper") and not keys & _CLIMBING
    ]
    assert without == []


@pytest.mark.parametrize("short_notice", [False, True], ids=["full", "short_notice"])
@pytest.mark.parametrize("ambition", list(AmbitionLevel))
@pytest.mark.parametrize("goal_type", _TRAIL_GOALS)
def test_every_build_peak_and_taper_week_of_a_default_trail_plan_climbs(
    goal_type, ambition, short_notice
):
    # Base weeks are out of scope: a base can alternate its climb with a
    # gentler session by design.
    if (goal_type, ambition) in _TOO_SMALL_TO_CLIMB:
        pytest.skip("its weeks cannot hold a climbing session at its share")
    horizon = GOAL_PROFILES[goal_type].min_horizon_weeks
    start = date(2026, 9, 7)
    result = generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal_type, start + timedelta(weeks=horizon, days=-1), "A")],
            training_start=start,
            ambition=ambition,
            athlete=default_profile_for(goal_type, ambition),
            prior_training_weeks=horizon - 5 if short_notice else 0,
        )
    )
    without = [
        (phase.value, mode)
        for phase, mode, keys in _weeks(result)
        if phase in (PhaseType.BUILD, PhaseType.PEAK, PhaseType.TAPER)
        and mode in (WeekMode.LOAD.value, WeekMode.TAPER.value)
        and not keys & _CLIMBING
    ]
    assert without == []
