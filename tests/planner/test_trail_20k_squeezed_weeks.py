"""A squeezed trail 20K basic week keeps its climb and books no shell of a run.

The plans the scorecard sweeps for an athlete at 0.75 of the default volume
squeeze their weeks. Their taper week must keep the hill session (the menu's one quality
session, ``key`` 0.45 for basic) and shed easy running instead, and no
steady or progression run may book under its 30-minute floor: a shorter one
turns optional whole (R-SESS-037).
"""

from __future__ import annotations

import pytest

from devtools.quality.planner.score_quality import (
    build_default_scenario,
    build_low_volume_scenario,
)
from endury.planner import generate_plan
from endury.planner.foundations.types import ActivityKey, AmbitionLevel, GoalType
from endury.planner.sessions.session_generator import (
    ACTIVITY_FLOOR_MINUTES,
    _activity_floor_hours,
)

_RHYTHM = frozenset({ActivityKey.RUN_AEROBIC_TEMPO, ActivityKey.RUN_PROGRESSION})


def _plan(weeks: int, low_volume: bool):
    build = build_low_volume_scenario if low_volume else build_default_scenario
    return generate_plan(
        build(weeks, AmbitionLevel.BASIC, GoalType.TRAIL_20K).to_request()
    )


@pytest.mark.parametrize(("weeks", "low_volume"), [(12, True), (16, True)])
def test_a_squeezed_taper_week_keeps_its_hill_session(weeks: int, low_volume: bool):
    result = _plan(weeks, low_volume)
    taper = [s for s in result.sessions if s.week == len(result.phase_plan.weeks) - 2]

    booked = {s.activity_key for s in taper if s.optional_reason is None}
    assert {ActivityKey.RUN_HILL_REPEATS, ActivityKey.RUN_TRAIL_LONG} <= booked


@pytest.mark.parametrize(
    ("weeks", "low_volume"), [(12, True), (16, True), (20, True), (16, False)]
)
def test_no_steady_or_progression_run_books_under_its_floor(
    weeks: int, low_volume: bool
):
    result = _plan(weeks, low_volume)

    rhythm = [s for s in result.sessions if s.activity_key in _RHYTHM]
    assert rhythm
    for session in rhythm:
        assert session.target_hours * 60 >= ACTIVITY_FLOOR_MINUTES[session.activity_key]


def test_the_floor_table_lifts_only_cards_that_build_shorter():
    assert _activity_floor_hours(ActivityKey.RUN_AEROBIC_TEMPO) == 0.5
    assert _activity_floor_hours(ActivityKey.RUN_PROGRESSION) == 0.5
    assert _activity_floor_hours(ActivityKey.RUN_HILL_REPEATS) * 60 == pytest.approx(
        50.0
    )
