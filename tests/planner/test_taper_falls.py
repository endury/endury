"""A taper falls week over week: in hours and in each sport's longest session.

The case that found it: the default moderate marathon at 20 weeks ran its
two taper weeks at 3.61 h then 4.01 h (long run 75 then 85 min), because the
generator sizes a session from the minimum of its bounds in the first week
of a phase towards the maximum in its last, and the marathon taper menu
authored one variant per slot for the whole taper. A taper cuts volume step
by step (docs/research/triathlon_long_course.md §7, a two-step taper;
docs/research/endurance_methodology.md §6, an exponential one), so no taper
week may outgrow the one before it.

Only goals whose taper spans two or more taper weeks before the race week
can hold a pair (``GoalProfile.taper_weeks`` counts the race week); the
sweep runs each of them over every tier, every horizon the planner offers
(its minimum to its recommended maximum, in steps of two weeks), at the
tier's default volume and at one and a half times it.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta

import pytest

from endury.planner.foundations.request import AthleteState, PlanRequest, SportState
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    RaceGoal,
    SportType,
    WeekMode,
    is_maintenance_goal,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan

_START = date(2026, 9, 7)  # a Monday

#: Rounding room: bounds are minutes, the QP solves in hours.
_EPS = 1e-6

_TWO_TAPER_WEEK_GOALS = tuple(
    goal
    for goal, profile in GOAL_PROFILES.items()
    if not is_maintenance_goal(goal) and profile.taper_weeks >= 3
)


def _horizons(goal: GoalType) -> range:
    profile = GOAL_PROFILES[goal]
    assert profile.max_recommended_weeks is not None
    return range(profile.min_horizon_weeks, profile.max_recommended_weeks + 1, 2)


_CASES = [
    pytest.param(goal, ambition, weeks, id=f"{goal.value}-{ambition.value}-{weeks}w")
    for goal in _TWO_TAPER_WEEK_GOALS
    for ambition in AmbitionLevel
    for weeks in _horizons(goal)
]


def _athlete(goal: GoalType, ambition: AmbitionLevel, factor: float) -> AthleteState:
    """The tier's default athlete, with recent hours and longests times *factor*."""
    base = default_profile_for(goal, ambition)
    return AthleteState(
        sports={
            sport: SportState(
                recent_weekly_hours=state.recent_weekly_hours * factor,
                longest_recent_min={
                    family: round(minutes * factor)
                    for family, minutes in state.longest_recent_min.items()
                },
            )
            for sport, state in base.sports.items()
        }
    )


def test_the_sweep_covers_the_goals_with_two_taper_weeks():
    assert set(_TWO_TAPER_WEEK_GOALS) == {
        GoalType.RUNNING_MARATHON,
        GoalType.IRONMAN,
    }


@pytest.mark.parametrize("factor", [1.0, 1.5])
@pytest.mark.parametrize(("goal", "ambition", "weeks"), _CASES)
def test_no_taper_week_outgrows_the_week_before(goal, ambition, weeks, factor):
    result = generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, _START + timedelta(weeks=weeks, days=-1), "A")],
            training_start=_START,
            ambition=ambition,
            athlete=_athlete(goal, ambition, factor),
        )
    )
    taper = [w.week for w in result.phase_plan.weeks if w.mode == WeekMode.TAPER]
    assert len(taper) >= 2

    def hours(week: int, sport: SportType | None = None) -> list[float]:
        return [
            s.target_hours
            for s in result.sessions
            if s.week == week and sport in (None, s.sport)
        ]

    for before, after in itertools.pairwise(taper):
        assert sum(hours(after)) <= sum(hours(before)) + _EPS, (before, after)
        for sport in SportType:
            longest_after = max(hours(after, sport), default=0.0)
            longest_before = max(hours(before, sport), default=0.0)
            assert longest_after <= longest_before + _EPS, (sport, before, after)
