"""A short week keeps its sessions, optional, instead of dropping them (R-SESS-037).

When a week cannot fit a session at its card, the session stays in the plan
at its smallest card, marked optional with the reason, outside the load
model. Only a fitness test that cannot be done whole, a brick without its
ride and a session after the A race still leave. The case that
found it: at 0.3 of the default volume a plan held 7-64 fewer sessions than
the same plan at three times the volume. In a taper or race week only an easy
session may stay optional; a hard or long one still leaves.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from datetime import date, timedelta

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.foundations.request import PlanRequest
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    RaceGoal,
    WeekMode,
    is_maintenance_goal,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan
from endury.planner.sessions.session_generator import race_sim_weeks

_START = date(2026, 9, 7)  # a Monday
_TESTS = frozenset(
    {
        ActivityKey.RUN_THRESHOLD_TEST,
        ActivityKey.BIKE_FTP_TEST,
        ActivityKey.SWIM_CSS_TEST,
    }
)


def _plan(goal, ambition, volume):
    athlete = default_profile_for(goal, ambition)
    athlete = dataclasses.replace(
        athlete,
        sports={
            sport: dataclasses.replace(
                state, recent_weekly_hours=state.recent_weekly_hours * volume
            )
            for sport, state in athlete.sports.items()
        },
    )
    weeks = GOAL_PROFILES[goal].min_horizon_weeks + 4
    return generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, _START + timedelta(weeks=weeks, days=-1), "A")],
            training_start=_START,
            ambition=ambition,
            athlete=athlete,
        )
    )


@pytest.mark.parametrize("ambition", list(AmbitionLevel), ids=lambda a: a.value)
@pytest.mark.parametrize(
    "goal",
    [g for g in GOAL_PROFILES if not is_maintenance_goal(g)],
    ids=lambda g: g.value,
)
def test_a_small_athlete_keeps_every_session(goal, ambition):
    """The same plan at 0.3 and 3 times the default volume holds the same sessions."""
    ensure_registry_loaded()
    small, big = _plan(goal, ambition, 0.3), _plan(goal, ambition, 3.0)
    # A race-sim week resolves its rehearsal only when the week can hold it.
    rehearsal_weeks = race_sim_weeks(small.phase_plan.weeks)
    shedding = {
        w.week
        for w in small.phase_plan.weeks
        if w.mode in (WeekMode.TAPER, WeekMode.RACE)
    }

    def may_leave(s) -> bool:
        return s.week in shedding and get_activity(s.activity_key).family not in (
            ActivityFamily.EASY,
            ActivityFamily.RECOVERY,
        )

    def sessions(result):
        return Counter(
            (s.week, s.slot_name)
            for s in result.sessions
            if s.activity_key not in _TESTS
            and s.week not in rehearsal_weeks
            and not may_leave(s)
        )

    assert sessions(big) - sessions(small) == Counter()
    optional = [s for s in small.sessions if s.optional_reason]
    # Fixed at its card, never under its authored minimum's sliver.
    assert all(s.min_hours == s.max_hours == s.target_hours for s in optional)
    assert all(s.target_hours >= 10 / 60 for s in optional)
