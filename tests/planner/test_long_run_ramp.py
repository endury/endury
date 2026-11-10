"""A running long session grows from the one the athlete was booked (R-SESS-013).

Each load week's long run is at most one step, 10 % or 8 minutes, above the
long run booked in the load week before it: within a phase, across a
recovery week and into the next phase. The race rehearsal is the one
weekend allowed past the step (R-SESS-023), and a back-to-back day is a
second long run, not the next step. The case that found it: the default
moderate half marathon at its minimum horizon ran a 58-min long run, then
76 min into BUILD, because the ramp advanced at its full value while the
squeezed week booked less.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.foundations.request import PlanRequest
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    RaceGoal,
    SportType,
    WeekMode,
    is_maintenance_goal,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan
from endury.planner.sessions.menus import get_session_menu

_RACE_GOALS = tuple(g for g in GOAL_PROFILES if not is_maintenance_goal(g))

#: The QP books on a 5-minute grid and may round a long run up one step.
_GRID_MIN = 5.0


def _cases():
    for goal in _RACE_GOALS:
        for ambition in AmbitionLevel:
            for extra_weeks in (0, 8):
                for volume in (0.8, 1.0, 1.25):
                    yield pytest.param(
                        goal,
                        ambition,
                        extra_weeks,
                        volume,
                        id=f"{goal.value}-{ambition.value}-{extra_weeks}-{volume}",
                    )


def _rehearsals(goal: GoalType) -> set[str]:
    """The slot names of *goal*'s race rehearsals, across tiers."""
    return {
        s.slot_name
        for ambition in AmbitionLevel
        for s in get_session_menu(goal, PhaseType.PEAK, "load", ambition)
        if s.race_sim
    }


def _plan(goal, ambition, extra_weeks, volume):
    """The default athlete at *volume* of its hours and its longest sessions."""
    ensure_registry_loaded()
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
    weeks = GOAL_PROFILES[goal].min_horizon_weeks + extra_weeks
    return generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, start + timedelta(weeks=weeks, days=-1), "A")],
            training_start=start,
            ambition=ambition,
            athlete=athlete,
        )
    )


@pytest.mark.parametrize(("goal", "ambition", "extra_weeks", "volume"), list(_cases()))
def test_no_long_run_jumps_past_the_step(goal, ambition, extra_weeks, volume):
    """The default athlete at *volume* of its hours and its longest sessions."""
    result = _plan(goal, ambition, extra_weeks, volume)
    rehearsals = _rehearsals(goal)
    jumps = []
    booked: float | None = None
    for week in result.phase_plan.weeks:
        if week.mode != WeekMode.LOAD:
            continue
        longs = [
            s
            for s in result.sessions
            if s.week == week.week
            and s.sport == SportType.RUNNING
            and s.activity_key != ActivityKey.RUN_TRAIL_B2B
            and get_activity(s.activity_key).family == ActivityFamily.LONG
        ]
        if not longs:
            continue
        run = max(s.target_hours * 60 for s in longs)
        rehearsal = any(s.slot_name in rehearsals for s in longs)
        if booked is not None and not rehearsal:
            cap = booked + max(0.10 * booked, 8.0)
            if run > cap + _GRID_MIN:
                jumps.append((week.week, week.phase.value, round(booked), round(run)))
        booked = run
    assert jumps == []


#: Each sport's long session, whatever its phase calls it, and the step
#: floor its ramp grows by (R-SESS-013).
_LONG_SESSION = {
    SportType.CYCLING: (
        lambda key: get_activity(key).family == ActivityFamily.LONG,
        12.0,
    ),
    SportType.SWIMMING: (
        lambda key: key in (ActivityKey.SWIM_LONG, ActivityKey.SWIM_ENDURANCE),
        4.0,
    ),
}


@pytest.mark.parametrize("sport", list(_LONG_SESSION), ids=lambda s: s.value)
@pytest.mark.parametrize(
    ("goal", "ambition", "extra_weeks", "volume"),
    [
        c
        for c in _cases()
        if SportType.CYCLING in GOAL_PROFILES[c.values[0]].default_sport_split
    ],
)
def test_no_long_ride_or_swim_jumps_past_the_step(
    goal, ambition, extra_weeks, volume, sport
):
    """The long ride and the long swim grow as the long run does.

    The case that found it: the default moderate half-Ironman at 12 weeks
    rode 100, 110, 115 min in BASE, 107, 114, 126 in BUILD, where the
    sweet-spot ride ran its own ramp from the athlete's recent longest,
    then 185 into PEAK, where the race-pace ride booked its menu minimum.
    """
    is_long, step_floor = _LONG_SESSION[sport]
    result = _plan(goal, ambition, extra_weeks, volume)
    rehearsals = _rehearsals(goal)
    jumps = []
    booked: float | None = None
    for week in result.phase_plan.weeks:
        if week.mode != WeekMode.LOAD:
            continue
        longs = [
            s
            for s in result.sessions
            if s.week == week.week and s.sport == sport and is_long(s.activity_key)
        ]
        if not longs:
            continue
        long = max(s.target_hours * 60 for s in longs)
        rehearsal = any(s.slot_name in rehearsals for s in longs)
        if booked is not None and not rehearsal:
            cap = booked + max(0.10 * booked, step_floor)
            if long > cap + _GRID_MIN:
                jumps.append((week.week, week.phase.value, round(booked), round(long)))
        booked = long
    assert jumps == []
