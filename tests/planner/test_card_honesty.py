"""R-SESS-030 card-honesty sweep: rendered cards match solved durations.

The invariants below hold for every goal x tier at both a returner-low and
the goal-default capacity seed:

* a session card renders within the R-SESS-030 tolerance of its QP-solved
  duration (a partial floor lift may render up to ~1/0.85 of the booking);
* fixed-protocol tests book exactly their card duration or are dropped;
* a brick run's partner ride exists, is a ride, and shares its date;
* a swim card swims its menu's metres at the swimmer's CSS (1:40, 2:00
  and 2:30 per 100 m), the swim test's protocol booked at that CSS.

The full grid is ``slow`` (run by ``make test-scorecards`` and the
pre-push hook) and covers the plans the menus offer (``offered:``); a
one-goal-per-family returner subset runs in the default ``make test`` gate.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from endury.planner import PlanRequest, generate_plan
from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.materialize import (
    assignment_target_tss,
    workout_total_meters,
)
from endury.planner.activity.sets import set_occurrences, with_set
from endury.planner.foundations.request import AthleteState, SportState
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    RaceGoal,
    SessionOrigin,
    SportType,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for
from endury.planner.sessions.menus import BLUEPRINTS, get_session_menu
from endury.planner.sessions.session_generator import (
    FLOOR_DROP_TOLERANCE,
    _swim_minutes,
)
from endury.training.load import LoadMetricContext

_START = date(2026, 9, 7)  # a Monday

#: One goal per family for the fast default-gate subset.
_FAST_GOALS = (
    GoalType.RUNNING_10K,
    GoalType.TRAIL_20K,
    GoalType.SPRINT_TRIATHLON,
    GoalType.RUNNING_MAINTENANCE,
)


def _horizon_weeks(goal: GoalType) -> int:
    """A mid-length prep for *goal* (fixed 12-week block for maintenance)."""
    profile = GOAL_PROFILES[goal]
    if profile.max_recommended_weeks is None:
        return 12
    return min(profile.min_horizon_weeks + 8, profile.max_recommended_weeks)


def _returner_athlete(goal: GoalType, ambition: AmbitionLevel) -> AthleteState:
    """A low-capacity returner: a third of the default seed, short longests."""
    base = default_profile_for(goal, ambition)
    return AthleteState(
        sports={
            sport: SportState(
                recent_weekly_hours=state.recent_weekly_hours / 3.0,
                longest_recent_min={
                    family: max(15, int(minutes * 0.4))
                    for family, minutes in state.longest_recent_min.items()
                },
            )
            for sport, state in base.sports.items()
        }
    )


def _generate(goal: GoalType, ambition: AmbitionLevel, athlete: AthleteState):
    weeks = _horizon_weeks(goal)
    return generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, _START + timedelta(weeks=weeks, days=-1), "A")],
            training_start=_START,
            ambition=ambition,
            athlete=athlete,
        )
    )


def _assert_cards_honest(
    result, label: str, resolver: LoadMetricContext | None = None
) -> None:
    ensure_registry_loaded()
    by_slot = {s.slot_id: s for s in result.sessions}
    date_by_slot = {a.slot_id: a.date for a in result.assignments}
    # The athlete's cards: a recurring session type rotates its sets.
    occurrences = set_occurrences(result.assignments)
    for a in result.assignments:
        session = by_slot[a.slot_id]
        activity = with_set(get_activity(a.activity_key), occurrences[a.slot_id])
        tss = assignment_target_tss(activity, a.target_hours, resolver)
        card = activity.duration_minutes_exact(tss, resolver=resolver)
        solved = a.target_hours * 60.0
        where = f"{label} {a.date} {a.slot_id}"
        if session.origin is SessionOrigin.TEST:
            assert card == pytest.approx(solved, abs=0.1), (
                f"{where}: test books {solved:.1f} min but the fixed "
                f"protocol card is {card:.1f} min"
            )
            continue
        assert card <= solved / FLOOR_DROP_TOLERANCE + 0.5, (
            f"{where}: card {card:.1f} min renders beyond tolerance of the "
            f"solved {solved:.1f} min"
        )
        assert card >= solved * 0.75 - 0.5, (
            f"{where}: card {card:.1f} min renders far below the solved "
            f"{solved:.1f} min"
        )
    for session in result.sessions:
        if session.brick_partner is None:
            continue
        partner = by_slot.get(session.brick_partner)
        assert partner is not None, (
            f"{label} {session.slot_id}: brick partner {session.brick_partner} missing"
        )
        assert partner.sport is SportType.CYCLING, (
            f"{label} {session.slot_id}: brick partner "
            f"{session.brick_partner} is not a ride"
        )
        assert date_by_slot[session.slot_id] == date_by_slot[partner.slot_id], (
            f"{label} {session.slot_id}: brick and ride landed on different dates"
        )


@pytest.mark.slow
@pytest.mark.parametrize("seed", ["returner", "default"])
@pytest.mark.parametrize(
    ("goal", "ambition"),
    [
        (goal, ambition)
        for goal in GoalType
        for ambition in AmbitionLevel
        if ambition in BLUEPRINTS[goal].offered
    ],
    ids=lambda value: value.value,
)
def test_card_honesty_full_grid(
    goal: GoalType, ambition: AmbitionLevel, seed: str
) -> None:
    """Every offered goal x tier x seed emits only honestly renderable sessions."""
    athlete = (
        _returner_athlete(goal, ambition)
        if seed == "returner"
        else default_profile_for(goal, ambition)
    )
    result = _generate(goal, ambition, athlete)
    _assert_cards_honest(result, f"{goal.value}/{ambition.value}/{seed}")


@pytest.mark.parametrize("goal", _FAST_GOALS, ids=lambda g: g.value)
def test_card_honesty_returner_representatives(goal: GoalType) -> None:
    """Default-gate subset: one goal per family at the returner seed."""
    athlete = _returner_athlete(goal, AmbitionLevel.BASIC)
    result = _generate(goal, AmbitionLevel.BASIC, athlete)
    _assert_cards_honest(result, f"{goal.value}/basic/returner")


#: One 100 m step: the coarsest a swim card's distance moves.
_SWIM_STEP_SLACK_M = 100.0


def _menu_metres(goal: GoalType, ambition: AmbitionLevel) -> dict[str, tuple[int, int]]:
    """Each swim slot's authored metres across the goal's menus."""
    blueprint = BLUEPRINTS[goal]
    specs = [
        spec
        for phase, mode_key in blueprint.phases
        for spec in get_session_menu(goal, phase, mode_key, ambition)
        if spec.sport is SportType.SWIMMING and spec.slot_name
    ]
    out: dict[str, tuple[int, int]] = {}
    for spec in specs:
        lo, hi = out.get(spec.slot_name, (spec.min_meters, spec.max_meters))
        out[spec.slot_name] = (min(lo, spec.min_meters), max(hi, spec.max_meters))
    return out


def _assert_swims_hold_their_metres(
    result, goal: GoalType, ambition: AmbitionLevel, css: float, label: str
) -> None:
    """A swim card swims its menu's metres at the swimmer's CSS.

    Never more than the menu's most; never less than its least unless the
    week booked the swim under the minutes those metres take (a squeezed
    week, a long swim still ramping), one 100 m step of slack each way.
    """
    ensure_registry_loaded()
    resolver = LoadMetricContext(swim_css_sec_per_100m=css)
    metres = _menu_metres(goal, ambition)
    by_slot = {s.slot_id: s for s in result.sessions}
    occurrences = set_occurrences(result.assignments)
    for a in result.assignments:
        session = by_slot[a.slot_id]
        bounds = metres.get(session.slot_name or "")
        if bounds is None or session.origin is SessionOrigin.TEST:
            continue
        activity = with_set(get_activity(a.activity_key), occurrences[a.slot_id])
        tss = assignment_target_tss(activity, a.target_hours, resolver)
        card = workout_total_meters(activity.build(tss, resolver=resolver).steps)
        where = f"{label} {a.date} {session.slot_name} {a.activity_key.value}"
        assert card <= bounds[1] + _SWIM_STEP_SLACK_M, (
            f"{where}: card swims {card:.0f} m, the menu at most {bounds[1]} m"
        )
        least = _swim_minutes(a.activity_key, bounds[0], css)
        if a.target_hours * 60.0 >= least - 0.5:
            assert card >= bounds[0] - _SWIM_STEP_SLACK_M, (
                f"{where}: booked {a.target_hours * 60.0:.1f} min for the menu's "
                f"{bounds[0]} m, but the card swims {card:.0f} m"
            )


_SWIM_GOALS = [
    (goal, ambition)
    for goal in GoalType
    for ambition in AmbitionLevel
    if ambition in BLUEPRINTS[goal].offered
    and SportType.SWIMMING in default_profile_for(goal, ambition).sports
]


@pytest.mark.slow
@pytest.mark.parametrize("css", [100.0, 120.0, 150.0])
@pytest.mark.parametrize(
    ("goal", "ambition"), _SWIM_GOALS, ids=lambda value: value.value
)
def test_swims_hold_their_menu_metres(
    goal: GoalType, ambition: AmbitionLevel, css: float
) -> None:
    """Every offered swim plan's cards swim their menu metres at three CSS."""
    result = _generate_at_css(goal, ambition, css)
    label = f"{goal.value}/{ambition.value}/css {css:.0f}"
    _assert_swims_hold_their_metres(result, goal, ambition, css, label)
    _assert_cards_honest(result, label, LoadMetricContext(swim_css_sec_per_100m=css))


def test_swims_hold_their_menu_metres_for_a_slow_swimmer() -> None:
    """Default-gate case: a 2:30 swimmer's half-Ironman swims its metres."""
    goal, ambition, css = GoalType.HALF_IRONMAN, AmbitionLevel.MODERATE, 150.0
    result = _generate_at_css(goal, ambition, css)
    label = f"{goal.value}/{ambition.value}/css {css:.0f}"
    _assert_swims_hold_their_metres(result, goal, ambition, css, label)
    _assert_cards_honest(result, label, LoadMetricContext(swim_css_sec_per_100m=css))


def _generate_at_css(goal: GoalType, ambition: AmbitionLevel, css: float):
    """The goal's default athlete, swimming at *css* sec/100m."""
    base = default_profile_for(goal, ambition)
    sports = dict(base.sports)
    sports[SportType.SWIMMING] = dataclasses.replace(
        sports[SportType.SWIMMING], css_sec_per_100m=css
    )
    return _generate(goal, ambition, AthleteState(sports=sports))
