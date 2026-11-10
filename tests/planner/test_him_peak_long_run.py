"""Every half-Ironman PEAK load week keeps its long run (R-SESS-033).

The PEAK long run is a key session: a squeezed week books it at its target,
at most its share of the week, and shrinks the others. The case that found
it: half-Ironman athletes at half the default volume joining five weeks out
lost the long run from 6 of their PEAK load weeks, since the squeezed week
could not book it near its card (R-SESS-030).
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from endury.planner.foundations.request import PlanRequest
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PhaseType,
    RaceGoal,
    WeekMode,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for, generate_plan

_START = date(2026, 9, 7)  # a Monday
_GOAL = GoalType.HALF_IRONMAN


@pytest.mark.parametrize("volume", [0.5, 0.75, 1.0])
@pytest.mark.parametrize("ambition", list(AmbitionLevel), ids=lambda a: a.value)
@pytest.mark.parametrize("joined", [False, True])
def test_every_peak_load_week_holds_its_long_run(volume, ambition, joined):
    """The default athlete at *volume*, fresh or joining five weeks out."""
    athlete = default_profile_for(_GOAL, ambition)
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
    weeks = GOAL_PROFILES[_GOAL].min_horizon_weeks
    prior = weeks - 5 if joined else 0
    result = generate_plan(
        PlanRequest(
            goals=[RaceGoal(_GOAL, _START + timedelta(weeks=weeks, days=-1), "A")],
            training_start=_START + timedelta(weeks=prior),
            ambition=ambition,
            athlete=athlete,
            prior_training_weeks=prior,
        )
    )
    missing = [
        w.week
        for w in result.phase_plan.weeks
        if w.phase == PhaseType.PEAK
        and w.mode == WeekMode.LOAD
        and not any(
            s.week == w.week
            and s.slot_name
            in (
                "him_peak_wed_long_run",
                "him_mod_peak_sun_long_run",
                "him_comp_peak_sat_long_run",
            )
            for s in result.sessions
        )
    ]
    assert missing == []
