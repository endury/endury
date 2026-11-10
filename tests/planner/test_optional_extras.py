"""A menu's optional extras ride on top of the plan (R-SESS-040).

A slot the menu authors ``optional: true`` (the trail menus' rides) is booked
at its target, marked optional, and never enters the week's capacity fit or
the load model: the rest of the plan is the same with or without it.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from endury.planner.foundations.request import PlanRequest
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PlacedSession,
    RaceGoal,
    SportType,
    WeekMode,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan
from endury.planner.sessions import session_generator
from endury.planner.sessions.session_generator import OPTIONAL_MENU_WHY

_START = date(2026, 9, 7)  # a Monday
_TRAIL = [
    GoalType.TRAIL_20K,
    GoalType.TRAIL_50K,
    GoalType.TRAIL_100K,
    GoalType.TRAIL_MAINTENANCE,
]


def _request(goal: GoalType, ambition: AmbitionLevel, volume: float = 1.0):
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
    return PlanRequest(
        goals=[RaceGoal(goal, _START + timedelta(weeks=weeks, days=-1), "A")],
        training_start=_START,
        ambition=ambition,
        athlete=athlete,
    )


def _extras(sessions: list[PlacedSession]) -> list[PlacedSession]:
    return [s for s in sessions if s.optional_reason == OPTIONAL_MENU_WHY]


def _unnamed(sessions: list[PlacedSession]) -> list[PlacedSession]:
    """The sessions without their slot ids, which count the menu's specs."""
    return [dataclasses.replace(s, slot_id="", brick_partner=None) for s in sessions]


def _shape(sessions: list[PlacedSession]) -> list[tuple]:
    """Each session's place and kind, hours aside."""
    return [
        (s.week, s.day_of_week, s.slot_name, s.activity_key, s.optional_reason)
        for s in sessions
    ]


def _hours(sessions: list[PlacedSession]) -> list[float]:
    return [h for s in sessions for h in (s.min_hours, s.target_hours, s.max_hours)]


@pytest.mark.parametrize("ambition", list(AmbitionLevel), ids=lambda a: a.value)
@pytest.mark.parametrize("goal", _TRAIL, ids=lambda g: g.value)
def test_the_rides_change_nothing_else(goal, ambition, monkeypatch):
    """Every trail plan offers its rides, and the plan around them is unchanged."""
    request = _request(goal, ambition)
    plan = generate_plan(request)
    menu = session_generator.get_session_menu
    monkeypatch.setattr(
        session_generator,
        "get_session_menu",
        lambda *args: tuple(s for s in menu(*args) if not s.optional),
    )
    without = generate_plan(request)

    extras = _extras(plan.sessions)
    assert extras
    assert {s.sport for s in extras} == {SportType.CYCLING}
    modes = {w.week: w.mode for w in plan.phase_plan.weeks}
    assert {modes[s.week] for s in extras} <= {WeekMode.LOAD, WeekMode.RECOVERY}
    rest = [s for s in plan.sessions if s not in extras]
    assert _shape(rest) == _shape(without.sessions)
    # The solver meets the same weeks in another order: hours agree to seconds.
    assert _hours(rest) == pytest.approx(_hours(without.sessions), abs=0.01)


@pytest.mark.parametrize("goal", _TRAIL, ids=lambda g: g.value)
def test_a_short_week_books_the_rides_whole(goal):
    """The rides keep their target whatever the athlete's volume."""
    small = generate_plan(_request(goal, AmbitionLevel.BASIC, 0.3))
    big = generate_plan(_request(goal, AmbitionLevel.BASIC, 3.0))
    assert _unnamed(_extras(small.sessions)) == _unnamed(_extras(big.sessions))
