"""A race week shrinks with the athlete (R-SESS-036).

A race week is at most its goal's share of the biggest load week the plan
booked: about 20-40 % of the peak week for road running and triathlon, under
20 % for the ultra trail races (docs/research/short_course.md §3-4,
triathlon_long_course.md §7, trail_running.md §11.2). It keeps its strides or
opener and its easy sessions, all shortened alike; a session never goes
under its card nor under half its authored length, so a race week already at
those floors may stay over the share.
The case that found it: every race week of a tier was one fixed size, so the
basic half marathon's was 0.47 of the peak week at the default volume and
0.59 at half of it.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from datetime import date, timedelta

import pytest

from endury.planner.activity.config import DEFAULT_SWIM_CSS_SEC_PER_100M
from endury.planner.foundations.request import PlanRequest
from endury.planner.foundations.types import (
    ActivityKey,
    AmbitionLevel,
    PhaseType,
    RaceGoal,
    WeekMode,
    is_maintenance_goal,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan
from endury.planner.sessions.menus import get_session_menu
from endury.planner.sessions.session_generator import (
    FILLER_MIN_SCALE,
    RACE_WEEK_SHARE,
    _activity_floor_hours,
    _swim_minutes,
)

_START = date(2026, 9, 7)  # a Monday
_OPENERS = frozenset({ActivityKey.RUN_STRIDES, ActivityKey.BIKE_OPENER})
#: The QP books on a 5-minute grid and may round a session up one step.
_GRID_H = 5.0 / 60.0
#: The generator sizes the race week against the peak week it booked; the
#: solver may then book that peak a few percent lighter (its smoothing and
#: freshness terms), which the generator cannot see.
_SOLVER_TRIM = 1.05


def _cases():
    for goal in GOAL_PROFILES:
        if is_maintenance_goal(goal):
            continue
        for ambition in AmbitionLevel:
            for volume in (0.5, 0.75, 1.0, 1.25, 1.5):
                yield pytest.param(
                    goal, ambition, volume, id=f"{goal.value}-{ambition.value}-{volume}"
                )


@pytest.mark.parametrize(("goal", "ambition", "volume"), list(_cases()))
def test_race_week_is_at_most_its_share_of_the_peak(goal, ambition, volume):
    """The default athlete at *volume* of its hours, at the minimum horizon."""
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
    result = generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, _START + timedelta(weeks=weeks, days=-1), "A")],
            training_start=_START,
            ambition=ambition,
            athlete=athlete,
        )
    )
    hours: dict[int, float] = defaultdict(float)
    for s in result.sessions:
        hours[s.week] += s.target_hours
    peak = max(
        hours[w.week] for w in result.phase_plan.weeks if w.mode == WeekMode.LOAD
    )
    race_week = result.phase_plan.weeks[-1]
    assert race_week.mode == WeekMode.RACE
    race = [s for s in result.sessions if s.week == race_week.week]
    menu = get_session_menu(goal, PhaseType.TAPER, "race", ambition)
    authored = {
        s.slot_name: s.max_minutes / 60.0
        if s.max_minutes
        else _swim_minutes(s.activity_key, s.max_meters, DEFAULT_SWIM_CSS_SEC_PER_100M)
        / 60.0
        for s in menu
    }
    at_cards = all(
        s.target_hours
        <= max(
            _activity_floor_hours(s.activity_key),
            FILLER_MIN_SCALE * authored[s.slot_name],
        )
        + _GRID_H
        for s in race
    )
    cap = RACE_WEEK_SHARE[goal] * peak * _SOLVER_TRIM
    assert at_cards or hours[race_week.week] <= cap + _GRID_H
    if any(s.activity_key in _OPENERS for s in menu):
        assert any(s.activity_key in _OPENERS for s in race)
