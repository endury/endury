"""A rehearsal never jumps far past the long sessions before it (R-SESS-023).

A race-rehearsal session on a ramp (the trail rehearsal long runs, the
Ironman big ride, long run and race-distance swim) fires only when its
minimum is at most a quarter longer than the least its progression was last
booked or the athlete's recent longest, whichever is longer; a variant may
author its own step (`max_step`, the Ironman basic big ride 0.3). Otherwise
the week keeps its routine sessions. For the default
athletes here (longest 60 min) that bounds the rehearsal by the long
sessions booked before it. Before, a rehearsal fired once its progression had ever
reached any phase's minimum: the basic trail 100K at 14 weeks ran 112 min
and then a 250-min rehearsal.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from endury.planner.foundations.request import PlanRequest
from endury.planner.foundations.types import AmbitionLevel, GoalType, RaceGoal
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan
from endury.planner.sessions.menus import BLUEPRINTS
from endury.planner.sessions.session_generator import REHEARSAL_STEP_FRACTION

_START = date(2026, 9, 7)  # a Monday


def _progressions(goal: GoalType) -> tuple[dict[str, str], dict[str, float]]:
    """Each slot's progression, and each rehearsal slot's step."""
    slots: dict[str, str] = {}
    steps: dict[str, float] = {}
    for menu in BLUEPRINTS[goal].phases.values():
        for slot in menu.slots:
            for v in slot.variants:
                if v.progression is None:
                    continue
                slots[slot.name] = v.progression.name
                if v.race_sim:
                    steps[slot.name] = v.max_step or REHEARSAL_STEP_FRACTION
    return slots, steps


_GOALS = [
    goal for goal in BLUEPRINTS if _progressions(goal)[1] and goal in GOAL_PROFILES
]


def _plan(goal: GoalType, ambition: AmbitionLevel, volume: float, weeks: int):
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
    return generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, _START + timedelta(weeks=weeks, days=-1), "A")],
            training_start=_START,
            ambition=ambition,
            athlete=athlete,
        )
    )


@pytest.mark.parametrize("volume", [0.75, 1.0])
@pytest.mark.parametrize("ambition", list(AmbitionLevel), ids=lambda a: a.value)
@pytest.mark.parametrize("goal", _GOALS, ids=lambda g: g.value)
def test_no_rehearsal_jumps_past_its_step(goal, ambition, volume):
    """At every other horizon the goal offers."""
    profile = GOAL_PROFILES[goal]
    slots, steps = _progressions(goal)
    for weeks in range(profile.min_horizon_weeks, profile.max_recommended_weeks + 1, 2):
        result = _plan(goal, ambition, volume, weeks)
        booked = [s for s in result.sessions if s.optional_reason is None]
        for sim in booked:
            if sim.slot_name not in steps:
                continue
            progression = slots[sim.slot_name]
            longest = max(
                (
                    s.target_hours
                    for s in booked
                    if s.week < sim.week and slots.get(s.slot_name) == progression
                ),
                default=0.0,
            )
            assert sim.target_hours <= longest * (1 + steps[sim.slot_name]) + 1e-6, (
                f"{weeks} wk: {sim.slot_name} {sim.target_hours * 60:.0f} min "
                f"after {longest * 60:.0f}"
            )


def test_a_big_weekend_out_of_reach_stays_routine():
    """The basic Ironman at 0.75 of its volume, 22 weeks: a 175-min long ride.

    Its 270-min big ride would be a 54 % jump, past the ride's own 0.3 step,
    so the race-sim week keeps its routine ride and run.
    """
    result = _plan(GoalType.IRONMAN, AmbitionLevel.BASIC, 0.75, 22)
    slots = {s.slot_name for s in result.sessions}
    assert "basic_peak_sat_bike_big" not in slots
    assert "basic_peak_sun_long_run_big" not in slots
