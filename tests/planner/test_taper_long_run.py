"""A taper keeps a long run, shorter than the plan's longest (R-SESS-034, R-PP-003).

The taper cuts volume and keeps its quality (docs/research/endurance_methodology.md
§6), so no taper week may hold a long run longer than the longest long run of the
load weeks before it. The case that found it: the half-Ironman taper long run
was authored at 65 min and more, above a small athlete's peak long run (the basic
half-Ironman at half the default volume, joining five weeks out, ran a 37-min PEAK
long run, then a 55-min taper one).
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.foundations.request import PlanRequest
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    RaceGoal,
    SportType,
    WeekMode,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan

_START = date(2026, 9, 7)  # a Monday
#: The QP books on a 5-minute grid and may round a session up one step.
_GRID_MIN = 5.0


def _cases():
    for goal in (GoalType.HALF_IRONMAN, GoalType.RUNNING_HALF_MARATHON):
        for ambition in AmbitionLevel:
            for volume in (0.5, 0.75, 1.0):
                for joined in (False, True):
                    yield pytest.param(
                        goal,
                        ambition,
                        volume,
                        joined,
                        id=f"{goal.value}-{ambition.value}-{volume}-{joined}",
                    )


@pytest.mark.parametrize(("goal", "ambition", "volume", "joined"), list(_cases()))
def test_taper_keeps_a_shorter_long_run(goal, ambition, volume, joined):
    """The default athlete at *volume*, fresh or joining five weeks out."""
    ensure_registry_loaded()
    athlete = default_profile_for(goal, ambition)
    athlete = dataclasses.replace(
        athlete,
        sports={
            sport: dataclasses.replace(
                state,
                recent_weekly_hours=state.recent_weekly_hours * volume,
                longest_recent_min={
                    f: max(10, round(m * volume))
                    for f, m in state.longest_recent_min.items()
                },
            )
            for sport, state in athlete.sports.items()
        },
    )
    weeks = GOAL_PROFILES[goal].min_horizon_weeks
    prior = weeks - 5 if joined else 0
    result = generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, _START + timedelta(weeks=weeks, days=-1), "A")],
            training_start=_START + timedelta(weeks=prior),
            ambition=ambition,
            athlete=athlete,
            prior_training_weeks=prior,
        )
    )

    def long_run(week: int) -> float:
        return max(
            (
                s.target_hours * 60
                for s in result.sessions
                if s.week == week
                and s.sport == SportType.RUNNING
                and get_activity(s.activity_key).family == ActivityFamily.LONG
            ),
            default=0.0,
        )

    weeks_ = result.phase_plan.weeks
    peak = max(long_run(w.week) for w in weeks_ if w.mode == WeekMode.LOAD)
    tapers = [long_run(w.week) for w in weeks_ if w.mode == WeekMode.TAPER]
    # A plan whose squeezed load weeks booked no long run has none to ease.
    floor = 0.0 if peak == 0.0 else 1e-9
    assert all(floor <= t <= peak + _GRID_MIN for t in tapers), (round(peak), tapers)
