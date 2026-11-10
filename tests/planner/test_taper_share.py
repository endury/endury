"""A taper week sheds volume with the athlete (R-SESS-038).

A taper week books at most 0.75 of the biggest load week booked before it,
the top of the 50-75 % the research gives for taper weeks, and never more
than the taper week before it. Volume falls, intensity holds: only its easy
and long sessions shorten, and a taper week whose floors cannot reach the
share is left as it was. The case that found it: a one-week taper is booked at its menu
minimums, which do not scale with the athlete, so the basic trail 50K at
0.75 of the default volume ran a 3.3 h taper after a 3.9 h peak (0.85).
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from datetime import date, timedelta
from itertools import pairwise

import pytest

from endury.planner.activity import get_activity
from endury.planner.foundations.request import PlanRequest
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    RaceGoal,
    WeekMode,
    is_maintenance_goal,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan
from endury.planner.sessions import session_generator
from endury.planner.sessions.session_generator import TAPER_WEEK_SHARE

_START = date(2026, 9, 7)  # a Monday
#: The QP books on a 5-minute grid, and the generator sizes the taper against
#: the peak it booked, which the solver may book a few percent lighter.
_GRID_H = 5.0 / 60.0
_SOLVER_TRIM = 1.05
#: A kept session can take a few percent off the week's minimums (the
#: capacity fit scales them together), so a quality session may book a
#: little under what it booked before.
_QUALITY_TRIM = 0.95
_VOLUME = {ActivityFamily.EASY, ActivityFamily.RECOVERY, ActivityFamily.LONG}


def _hours(goal, ambition, volume):
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
    quality: dict[int, dict[str, float]] = defaultdict(dict)
    for s in result.sessions:
        hours[s.week] += s.target_hours
        if get_activity(s.activity_key).family not in _VOLUME:
            quality[s.week][s.slot_name] = s.target_hours
    peak = max(
        hours[w.week] for w in result.phase_plan.weeks if w.mode == WeekMode.LOAD
    )
    tapers = [w.week for w in result.phase_plan.weeks if w.mode == WeekMode.TAPER]
    return peak, [hours[w] for w in tapers], [quality[w] for w in tapers]


def _cases():
    for goal in GOAL_PROFILES:
        if is_maintenance_goal(goal):
            continue
        for ambition in AmbitionLevel:
            for volume in (0.5, 0.75, 1.0):
                yield pytest.param(
                    goal, ambition, volume, id=f"{goal.value}-{ambition.value}-{volume}"
                )


@pytest.mark.parametrize(("goal", "ambition", "volume"), list(_cases()))
def test_a_taper_week_sheds_volume_and_holds_intensity(
    goal, ambition, volume, monkeypatch
):
    """The default athlete at *volume*, at the minimum horizon.

    Against the same plan without the taper shaping, each taper week is at
    most the larger of its cap and what it booked before, and keeps every
    quality session at its length, give or take the solver's trim; the tapers fall week over week.
    """
    peak, tapers, quality = _hours(goal, ambition, volume)
    monkeypatch.setattr(
        session_generator, "_shape_taper_week", lambda sessions, *_: sessions
    )
    _peak, before, quality_before = _hours(goal, ambition, volume)
    cap = TAPER_WEEK_SHARE * peak * _SOLVER_TRIM + _GRID_H
    assert all(
        t <= max(cap, b + _GRID_H) for t, b in zip(tapers, before, strict=True)
    ), (round(peak, 2), tapers, before)
    # Every quality session stays, within the solver's trim of its length.
    assert all(
        qs.get(slot, 0.0) >= _QUALITY_TRIM * hours
        for qs, bs in zip(quality, quality_before, strict=True)
        for slot, hours in bs.items()
    )
    assert all(later <= earlier + _GRID_H for earlier, later in pairwise(tapers))


def test_a_small_trail_athlete_tapers():
    """The case that found it: 3.3 h after a 3.9 h peak, now inside the share."""
    peak, tapers, _quality = _hours(GoalType.TRAIL_50K, AmbitionLevel.BASIC, 0.75)
    assert tapers[0] <= TAPER_WEEK_SHARE * peak * _SOLVER_TRIM + _GRID_H
