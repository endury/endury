"""Long-course day-layout regressions (rest day / hard-day stacking / brick chaining).

The import-time lints in ``menus.contracts`` already replay every weekly
layout a menu can emit, but they only forbid *within-sport* collisions.
These tests pin the three cross-cutting layout properties the planner
scorecard grades:

* ``rest_days`` — every training week leaves a day empty.
* ``cross_sport_hard_day_stacking`` — no day carries a hard ride and a
  hard run.
* ``brick_pairs_with_key_ride`` — a race-tempo brick sits on the day of
  the week's key (longest) ride.

Plus one property no instrument grades at all: long runs must not
end up on consecutive calendar days across a *week boundary*. The lints
wrap Sunday round to the same menu's Monday, so they see a repeated LOAD
week but never a phase transition; the scorecard grades week shapes, not
the seams between them.

They read the resolved weekly layouts (frac filter + slot-group
resolution) the same way ``contracts._check_tier_days`` does, so a menu
edit that regresses one fails here rather than only in the scorecard.
"""

from __future__ import annotations

from itertools import product

import pytest

from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    SportType,
)
from endury.planner.sessions.menus import BLUEPRINTS, SessionSpec, get_session_menu
from endury.planner.sessions.menus.resolve import active_specs

#: Phase fractions covering every frac window boundary the long-course
#: menus author, plus the midpoints between them (same probe grid as
#: ``test_brick_partner_is_colocated_bike_for_every_goal_and_week_shape``).
_PROBE_FRACS = (0.0, 0.2, 0.35, 0.4, 0.5, 0.6, 0.65, 0.7, 0.8, 1.0)

#: Week modes that must leave a rest day. TAPER and race weeks compress
#: toward race day and are graded by the taper rules instead.
_TRAINING_MODES = ("load", "recovery")

#: Race-tempo bricks: the ones doctrine chains to the week's key ride.
_TEMPO_BRICK_KEYS = frozenset(
    {ActivityKey.RUN_BRICK_HIM_TEMPO, ActivityKey.RUN_BRICK_IM_TEMPO}
)

_LONG_COURSE = (GoalType.HALF_IRONMAN, GoalType.IRONMAN)


def _cadences(specs) -> list[frozenset[int]]:
    """No cadence firing, then each interval the specs name firing."""
    every = sorted({s.cadence_weeks for s in specs if s.cadence_weeks})
    return [frozenset()] + [frozenset({n}) for n in every]


def _is_hard(spec: SessionSpec) -> bool:
    """True when *spec* can fire a physiologically hard session.

    Rotation-aware, like ``contracts._is_hard_spec``: a rotated-in key
    fires on real weeks, so any hard member makes the spec hard.

    Args:
        spec: The resolved session spec.
    """
    ensure_registry_loaded()
    return any(
        get_activity(key).hardness.is_hard_physiologically
        for key in (spec.activity_key, *spec.rotation)
    )


def _layouts(goal: GoalType, modes: tuple[str, ...]):
    """Yield every resolved weekly layout the *goal*'s menus can emit.

    Args:
        goal: Goal whose blueprint to walk.
        modes: ``mode_key`` values to include.

    Yields:
        ``(context, layout)`` pairs — a human-readable location and the
        list of specs the generator would place that week.
    """
    blueprint = BLUEPRINTS[goal]
    for phase, mode_key in blueprint.phases:
        if mode_key not in modes:
            continue
        for ambition in AmbitionLevel:
            specs = get_session_menu(goal, phase, mode_key, ambition)
            for frac in _PROBE_FRACS:
                for sim_week, cadence in product((False, True), _cadences(specs)):
                    ctx = (
                        f"{goal.value}/{phase.value}/{mode_key}/"
                        f"{ambition.value} frac={frac} sim={sim_week}"
                    )
                    yield ctx, list(active_specs(list(specs), frac, sim_week, cadence))


def test_half_ironman_training_weeks_leave_a_rest_day():
    """Every LOAD/RECOVERY layout must leave at least one weekday empty.

    Rest day: the half-Ironman menus scheduled 7/7 days in every week while
    the rest_days band asks for 0.94-2.00 empty days a week.

    Half-Ironman only. The Ironman COMPETITIVE menus still author 7/7
    weeks outside early PEAK — ``rest_days`` reads 0.2904
    there and holds that as a live defect; fixing it is its own
    re-authoring turn, not this one.
    """
    for ctx, layout in _layouts(GoalType.HALF_IRONMAN, _TRAINING_MODES):
        days = {spec.day_hint for spec in layout}
        assert len(days) <= 6, f"no rest day: sessions on all 7 weekdays [{ctx}]"


@pytest.mark.parametrize("goal", _LONG_COURSE, ids=lambda g: g.value)
def test_long_course_never_stacks_a_hard_ride_and_a_hard_run(goal):
    """No authored day may carry a hard ride and a hard run.

    The maintainer's rule from the Ironman menu review, for both long-course
    goals since 2026-10-05: each hard day belongs to one land sport, and a
    hard swim may share it because swimming spares the legs. How often a
    plan stacks hard work from two sports is graded by the
    ``cross_sport_hard_day_stacking`` band (``bench/rulebook.yaml``).
    """
    for ctx, layout in _layouts(goal, ("load", "recovery", "taper", "race")):
        hard_sports: dict[int, set[SportType]] = {}
        for spec in layout:
            if spec.sport != SportType.SWIMMING and _is_hard(spec):
                hard_sports.setdefault(spec.day_hint, set()).add(spec.sport)
        for day, sports in sorted(hard_sports.items()):
            assert len(sports) == 1, (
                f"day {day} stacks hard "
                f"{sorted(s.value for s in sports)} sessions [{ctx}]"
            )


@pytest.mark.parametrize("goal", _LONG_COURSE, ids=lambda g: g.value)
def test_race_tempo_brick_sits_on_the_key_ride_day(goal):
    """A race-tempo brick must share its day with the week's longest ride.

    docs/research/triathlon.md, Brick Sessions makes the PEAK brick one
    session — race-duration bike plus race-pace run — so a race-pace
    brick behind a secondary spin rehearses the wrong ride. Scored over
    training weeks only: a TAPER brick behind a short spin is the taper's
    own shape.

    "Longest ride" is read off the authored upper bounds, which is what
    the menu controls; the QP sizes within them.
    """
    for ctx, layout in _layouts(goal, _TRAINING_MODES):
        rides = [s for s in layout if s.sport == SportType.CYCLING]
        if not rides:
            continue
        key_day = max(rides, key=lambda s: s.max_minutes).day_hint
        for spec in layout:
            if spec.activity_key in _TEMPO_BRICK_KEYS:
                assert spec.day_hint == key_day, (
                    f"race-tempo brick {spec.activity_key.value} on day "
                    f"{spec.day_hint} is not on the key ride's day "
                    f"{key_day} [{ctx}]"
                )


def test_ironman_competitive_peak_key_ride_brick_is_race_pace():
    """COMPETITIVE's PEAK brick off the key ride runs at race pace.

    docs/research/triathlon.md, Brick Sessions makes the PEAK brick a race
    simulation — race-duration bike plus race-pace run. COMPETITIVE runs
    at race pace throughout PEAK: ``RUN_BRICK_IM_TEMPO`` early,
    ``RUN_BRICK_LONG`` late.

    MODERATE and BASIC keep the short-to-long escalation: their PEAK
    weekends carry less bike volume, so the early-peak priority there is
    still the ride.
    """
    race_pace = {ActivityKey.RUN_BRICK_IM_TEMPO, ActivityKey.RUN_BRICK_LONG}
    for ctx, layout in _layouts(GoalType.IRONMAN, ("load",)):
        if f"/{AmbitionLevel.COMPETITIVE.value} " not in ctx:
            continue
        if f"/{PhaseType.PEAK.value}/" not in ctx:
            continue
        rides = [s for s in layout if s.sport == SportType.CYCLING]
        key_day = max(rides, key=lambda s: s.max_minutes).day_hint
        bricks = [s for s in layout if s.is_brick_pair and s.day_hint == key_day]
        assert bricks, f"no brick on the key ride's day {key_day} [{ctx}]"
        for brick in bricks:
            assert brick.activity_key in race_pace, (
                f"key-ride brick {brick.activity_key.value} is not a "
                f"race-pace brick [{ctx}]"
            )


def _fires_long_run(spec: SessionSpec) -> bool:
    """True when *spec* can fire a LONG-family running session.

    Rotation-aware, like :func:`_is_hard`.

    Args:
        spec: The resolved session spec.
    """
    ensure_registry_loaded()
    return any(
        get_activity(key).sport == SportType.RUNNING
        and get_activity(key).family == ActivityFamily.LONG
        for key in (spec.activity_key, *spec.rotation)
    )


@pytest.mark.parametrize("ambition", list(AmbitionLevel), ids=lambda a: a.value)
@pytest.mark.parametrize("goal", _LONG_COURSE, ids=lambda g: g.value)
def test_long_runs_never_abut_across_a_week_boundary(goal, ambition):
    """No goal+tier may author both a Sunday and a Monday long run.

    ``contracts._check_tier_days`` lints one menu at a time and wraps
    Sunday round to that same menu's own Monday, so it sees a repeated
    LOAD week but never a phase transition — a long run parked on the
    last BUILD Sunday and another on the first PEAK Monday reads clean on
    every lint even though the two land on consecutive calendar days.

    The check is the conservative shape: any Sunday long run anywhere in
    the goal's menus for this tier forbids a Monday long run anywhere
    else in them, without modelling which menus the phase planner can
    actually place back to back. Every goal and tier satisfies it today,
    so the over-approximation costs nothing and no future authoring pass
    can reintroduce the pairing.
    """
    sunday: list[str] = []
    monday: list[str] = []
    blueprint = BLUEPRINTS[goal]
    for phase, mode_key in blueprint.phases:
        specs = get_session_menu(goal, phase, mode_key, ambition)
        for frac in _PROBE_FRACS:
            for sim_week, cadence in product((False, True), _cadences(specs)):
                for spec in active_specs(list(specs), frac, sim_week, cadence):
                    if not _fires_long_run(spec):
                        continue
                    where = f"{phase.value}/{mode_key} frac={frac} {spec.slot_name}"
                    if spec.day_hint == 6:
                        sunday.append(where)
                    elif spec.day_hint == 0:
                        monday.append(where)
    assert not (sunday and monday), (
        f"{goal.value}/{ambition.value} authors a Sunday long run "
        f"({min(sunday)}) and a Monday long run "
        f"({min(monday)}); a week boundary puts them on "
        "consecutive days"
    )
