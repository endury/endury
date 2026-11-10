"""A load or taper swim covers at least its tier's floor on the card (R-SESS-039).

1500 m at BASIC, 1800 m at MODERATE, 2000 m at COMPETITIVE, in every
triathlon plan, whatever the athlete's volume: a squeezed week gives way
elsewhere, or moves a whole swim out, rather than booking a swim under the
floor. Recovery and race weeks, drills and the CSS test are exempt.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.materialize import materialized_swim_meters
from endury.planner.foundations.request import PlanRequest
from endury.planner.foundations.types import (
    ActivityKey,
    AmbitionLevel,
    GoalType,
    RaceGoal,
    SportType,
    WeekMode,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan
from endury.planner.sessions.menus.patterns import SWIM_FLOOR_METRES

_TRIATHLON = (
    GoalType.SPRINT_TRIATHLON,
    GoalType.OLYMPIC_TRIATHLON,
    GoalType.HALF_IRONMAN,
    GoalType.IRONMAN,
    GoalType.TRIATHLON_MAINTENANCE,
)
_EXEMPT = (ActivityKey.SWIM_DRILL, ActivityKey.SWIM_CSS_TEST)


def _plan(goal: GoalType, ambition: AmbitionLevel, volume: float):
    """The default athlete at *volume* of its hours and its longest sessions."""
    athlete = default_profile_for(goal, ambition)
    athlete = dataclasses.replace(
        athlete,
        sports={
            sport: dataclasses.replace(
                state,
                recent_weekly_hours=state.recent_weekly_hours * volume,
                longest_recent_min={
                    f: max(20, round(m * volume))
                    for f, m in state.longest_recent_min.items()
                },
            )
            for sport, state in athlete.sports.items()
        },
    )
    start = date(2026, 9, 7)
    weeks = GOAL_PROFILES[goal].min_horizon_weeks
    return generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, start + timedelta(weeks=weeks, days=-1), "A")],
            training_start=start,
            ambition=ambition,
            athlete=athlete,
        )
    )


@pytest.mark.parametrize("volume", [0.6, 1.0])
@pytest.mark.parametrize("ambition", list(AmbitionLevel))
@pytest.mark.parametrize("goal", _TRIATHLON)
def test_no_load_or_taper_swim_is_under_the_floor(goal, ambition, volume):
    ensure_registry_loaded()
    result = _plan(goal, ambition, volume)
    modes = {week.week: week.mode for week in result.phase_plan.weeks}
    short = [
        (s.week, s.activity_key.value, metres)
        for s in result.sessions
        if s.sport == SportType.SWIMMING
        and s.optional_reason is None
        and modes[s.week] in (WeekMode.LOAD, WeekMode.TAPER)
        and s.activity_key not in _EXEMPT
        and (
            metres := materialized_swim_meters(
                get_activity(s.activity_key), s.target_hours, None
            )
        )
        is not None
        and metres < SWIM_FLOOR_METRES[ambition]
    ]
    assert short == []
