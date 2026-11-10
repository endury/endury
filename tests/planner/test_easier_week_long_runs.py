"""An easier week's long run stays under the plan's longest so far (R-SESS-034).

In a recovery, taper or race week the long run is never longer than three
quarters of the longest long run the plan has reached before it, and before
the first long run the yardstick is the athlete's recent longest. The case
that found it: the default competitive trail 50K at 12 weeks ran a 140-min
taper long run after a 135-min peak, and a 90-min recovery long run after
long runs of 68, 76 and 84 min, because the trail menus sized those weeks
for an athlete already at 3-4 h.

The week's long run is its longest run: the 100K recovery weeks carry
theirs as an easy Saturday run.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.foundations.request import AthleteState, PlanRequest, SportState
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    RaceGoal,
    SportType,
    WeekMode,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import PlanResult, default_profile_for, generate_plan
from endury.planner.sessions.session_generator import _activity_floor_hours

_TRAIL_GOALS = (
    GoalType.TRAIL_20K,
    GoalType.TRAIL_50K,
    GoalType.TRAIL_100K,
)

_EASIER = frozenset(
    {WeekMode.RECOVERY, WeekMode.RACE_RECOVERY, WeekMode.TAPER, WeekMode.RACE}
)

#: Rounding room: bounds are minutes, the QP solves in hours.
_EPS = 1e-6


def _plan(goal: GoalType, ambition: AmbitionLevel, weeks: int) -> PlanResult:
    start = date(2026, 9, 7)
    return generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, start + timedelta(weeks=weeks, days=-1), "A")],
            training_start=start,
            ambition=ambition,
            athlete=default_profile_for(goal, ambition),
        )
    )


def _long_runs(result: PlanResult) -> list[tuple[WeekMode, float, float]]:
    """Each week's (mode, longest run, longest long run before it), in minutes.

    The yardstick is the longest long-family run of the load weeks before
    the week, or the athlete's recent longest before the first one.
    """
    ensure_registry_loaded()
    longest = 60.0  # the default profile's recent longest run
    rows = []
    for week in result.phase_plan.weeks:
        runs = [
            s
            for s in result.sessions
            if s.week == week.week and s.sport == SportType.RUNNING
        ]
        if week.mode in _EASIER and runs:
            rows.append((week.mode, max(s.target_hours for s in runs) * 60, longest))
        if week.mode == WeekMode.LOAD:
            longs = [
                s.target_hours * 60
                for s in runs
                if get_activity(s.activity_key).family == ActivityFamily.LONG
            ]
            longest = max([longest, *longs])
    return rows


def test_the_competitive_trail_50k_eases_its_long_run():
    result = _plan(GoalType.TRAIL_50K, AmbitionLevel.COMPETITIVE, 12)
    rows = _long_runs(result)
    assert [mode for mode, _, _ in rows] == [
        WeekMode.RECOVERY,
        WeekMode.RECOVERY,
        WeekMode.TAPER,
        WeekMode.RACE,
    ]
    for _mode, run, yardstick in rows:
        assert run <= 0.75 * yardstick + _EPS
    taper = next(run for mode, run, _ in rows if mode == WeekMode.TAPER)
    peak = next(y for mode, _, y in rows if mode == WeekMode.TAPER)
    assert taper < peak


@pytest.mark.parametrize("weeks", [12, 16])
def test_a_squeezed_recovery_week_keeps_its_long_run(weeks):
    """A returner's 36-min recovery week drops an easy run, not the long run."""
    run = default_profile_for(GoalType.TRAIL_20K, AmbitionLevel.BASIC).sports[
        SportType.RUNNING
    ]
    start = date(2026, 9, 7)
    result = generate_plan(
        PlanRequest(
            goals=[
                RaceGoal(
                    GoalType.TRAIL_20K, start + timedelta(weeks=weeks, days=-1), "A"
                )
            ],
            training_start=start,
            ambition=AmbitionLevel.BASIC,
            athlete=AthleteState(
                sports={
                    SportType.RUNNING: SportState(
                        recent_weekly_hours=run.recent_weekly_hours / 3,
                        longest_recent_min={ActivityFamily.LONG: 35},
                    )
                }
            ),
        )
    )
    easier = [
        w.week
        for w in result.phase_plan.weeks
        if w.mode in (WeekMode.RECOVERY, WeekMode.TAPER)
    ]
    assert easier
    for week in easier:
        keys = {s.activity_key for s in result.sessions if s.week == week}
        assert ActivityKey.RUN_TRAIL_LONG in keys, week


def _scaled_plan(
    goal: GoalType, ambition: AmbitionLevel, factor: float, short_notice: bool
) -> PlanResult:
    """The default athlete at *factor* of its volume and longest run.

    Short notice joins the plan five weeks out, as a real athlete might.
    """
    run = default_profile_for(goal, ambition).sports[SportType.RUNNING]
    horizon = GOAL_PROFILES[goal].min_horizon_weeks
    weeks = 5 if short_notice else horizon
    start = date(2026, 6, 1)
    return generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, start + timedelta(weeks=weeks, days=-1), "A")],
            training_start=start,
            ambition=ambition,
            athlete=AthleteState(
                sports={
                    SportType.RUNNING: SportState(
                        recent_weekly_hours=run.recent_weekly_hours * factor,
                        longest_recent_min={
                            f: max(20, round(m * factor))
                            for f, m in run.longest_recent_min.items()
                        },
                    )
                }
            ),
            prior_training_weeks=horizon - weeks,
        )
    )


@pytest.mark.parametrize("short_notice", [False, True], ids=["full", "short"])
@pytest.mark.parametrize("factor", [0.5, 0.8, 1.0])
@pytest.mark.parametrize("ambition", list(AmbitionLevel))
@pytest.mark.parametrize("goal", _TRAIL_GOALS)
def test_no_easy_run_outruns_an_easier_weeks_long_run(
    goal, ambition, factor, short_notice
):
    """The minutes a capped long run gives up are not re-spent on easy runs.

    Easy runs are bounded by the eased cap, not by the long run's solved
    length: the solver may place a squeezed long run a minute or three
    under the easy runs beside it, so one 5-minute grid step is allowed,
    and a card booked at its own floor (strides never build under 27 min)
    has no minutes to give back, so it is left out.
    """
    ensure_registry_loaded()
    result = _scaled_plan(goal, ambition, factor, short_notice)
    over = []
    for week in result.phase_plan.weeks:
        if week.mode not in (WeekMode.RECOVERY, WeekMode.TAPER):
            continue
        # The booked week; an optional card (R-SESS-037) keeps its authored
        # minimum and may outlast a long run the squeeze shortened.
        runs = [
            s
            for s in result.sessions
            if s.week == week.week
            and s.optional_reason is None
            and s.target_hours > _activity_floor_hours(s.activity_key) + _EPS
        ]

        def longest(family: ActivityFamily, runs=runs) -> float:
            return max(
                (
                    s.target_hours * 60
                    for s in runs
                    if get_activity(s.activity_key).family == family
                ),
                default=0.0,
            )

        long_run, easy_run = longest(ActivityFamily.LONG), longest(ActivityFamily.EASY)
        if long_run and easy_run > long_run + 5.0:
            over.append((week.week, week.mode.value, round(easy_run), round(long_run)))
    assert over == []


@pytest.mark.parametrize("extra_weeks", [0, 8])
@pytest.mark.parametrize("ambition", list(AmbitionLevel))
@pytest.mark.parametrize("goal", _TRAIL_GOALS)
def test_no_easier_trail_week_runs_long(goal, ambition, extra_weeks):
    weeks = GOAL_PROFILES[goal].min_horizon_weeks + extra_weeks
    over = [
        (mode.value, round(run), round(yardstick))
        for mode, run, yardstick in _long_runs(_plan(goal, ambition, weeks))
        if run > 0.75 * yardstick + _EPS
    ]
    assert over == []
