"""Pinned-scenario regression tests for the session-menu runtime.

These tests pin numeric session targets for hand-picked (goal, ambition,
weeks) scenarios so any future change to the blueprint, runtime, or
Progression model can be reviewed against a concrete number rather than a
vague "the plan still looks roughly the same" check.

Update the pinned values intentionally when behaviour changes; failure
is the regression-net trigger.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseRequest,
    PhaseType,
    RaceGoal,
    SportType,
)
from endury.planner.phase.phase_planner import plan_phases
from endury.planner.sessions.session_generator import generate_session_menu

_START = date(2026, 6, 1)
_LONGEST = {
    (SportType.RUNNING, ActivityFamily.LONG): 60,
    (SportType.CYCLING, ActivityFamily.LONG): 90,
    (SportType.SWIMMING, ActivityFamily.EASY): 30,
}

#: The generator rounds each session's nominal minutes to the nearest 5, so a
#: bound sitting between two lattice points can render up to half a step away.
_ROUNDING_HEADROOM_MIN = 2.5


def _ironman_competitive_24wk_sessions():
    goal = RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=24), "A")
    plan = plan_phases(
        PhaseRequest(
            goals=[goal],
            training_start=_START,
            ambition=AmbitionLevel.COMPETITIVE,
        )
    )
    return generate_session_menu(plan, _LONGEST, ambition=AmbitionLevel.COMPETITIVE)


def _half_ironman_sessions(ambition: AmbitionLevel, weeks: int = 20):
    goal = RaceGoal(GoalType.HALF_IRONMAN, _START + timedelta(weeks=weeks), "A")
    plan = plan_phases(
        PhaseRequest(goals=[goal], training_start=_START, ambition=ambition)
    )
    sessions = generate_session_menu(plan, _LONGEST, ambition=ambition)
    phase_of = {ws.week: ws.phase for ws in plan.weeks}
    return sessions, phase_of


def _realized_minutes(session) -> float:
    """Card duration the athlete actually gets for *session*'s solved hours."""
    ensure_registry_loaded()
    activity = get_activity(session.activity_key)
    target_tss = assignment_target_tss(activity, session.target_hours, None)
    return activity.duration_minutes_exact(target_tss, None)


def _rep_work_minutes(session) -> float:
    """Work minutes per repetition the flexible-rep shape lands on."""
    ensure_registry_loaded()
    activity = get_activity(session.activity_key)
    target_tss = assignment_target_tss(activity, session.target_hours, None)
    _, work_minutes = activity._solve_pattern(target_tss)
    return work_minutes


def test_ironman_long_bike_carries_state_across_base_build():
    """IM_LONG_BIKE state carries from BASE BIKE_LONG into BUILD
    BIKE_LONG_SWEET_SPOT.

    The first BUILD BIKE_LONG_SWEET_SPOT week should land at or above
    the BASE BIKE_LONG end-state, never below it.
    """
    sessions = _ironman_competitive_24wk_sessions()
    base_bikes = sorted(
        (s for s in sessions if s.activity_key == ActivityKey.BIKE_LONG),
        key=lambda s: s.week,
    )
    build_bikes = sorted(
        (s for s in sessions if s.activity_key == ActivityKey.BIKE_LONG_SWEET_SPOT),
        key=lambda s: s.week,
    )
    assert base_bikes, "expected at least one BASE BIKE_LONG session"
    assert build_bikes, "expected at least one BUILD BIKE_LONG_SWEET_SPOT session"

    base_end_h = base_bikes[-1].target_hours
    build_start_h = build_bikes[0].target_hours
    # Carry-over means the BUILD start does not fall below the BASE end.
    # Floor-only carry (no progress) is acceptable; regression below is not.
    assert build_start_h >= base_end_h - 1e-6, (
        f"BUILD BIKE_LONG_SWEET_SPOT week-1 ({build_start_h:.3f}h) "
        f"sits below BASE BIKE_LONG end-state ({base_end_h:.3f}h) "
        "— Step 4e expected continuous carry."
    )


def test_ironman_long_run_state_shared_across_run_long_progression():
    """RUN_LONG and RUN_LONG_PROGRESSION share IM_LONG_RUN state.

    The first RUN_LONG_PROGRESSION week in BUILD should not regress
    below the preceding RUN_LONG week.
    """
    sessions = _ironman_competitive_24wk_sessions()
    runs = sorted(
        (
            s
            for s in sessions
            if s.activity_key
            in (ActivityKey.RUN_LONG, ActivityKey.RUN_LONG_PROGRESSION)
        ),
        key=lambda s: s.week,
    )
    # Find the first RUN_LONG_PROGRESSION week.
    first_prog_idx = next(
        (
            i
            for i, s in enumerate(runs)
            if s.activity_key == ActivityKey.RUN_LONG_PROGRESSION
        ),
        None,
    )
    assert first_prog_idx is not None, (
        "pinned 24wk COMPETITIVE Ironman must contain a RUN_LONG_PROGRESSION week"
    )
    assert first_prog_idx > 0, (
        "pinned 24wk COMPETITIVE Ironman must have a RUN_LONG week before the "
        "first RUN_LONG_PROGRESSION week"
    )
    prev = runs[first_prog_idx - 1]
    curr = runs[first_prog_idx]
    assert curr.target_hours >= prev.target_hours - 1e-6, (
        f"RUN_LONG_PROGRESSION week-{curr.week} ({curr.target_hours:.3f}h) "
        f"dropped below preceding RUN_LONG week-{prev.week} "
        f"({prev.target_hours:.3f}h) — shared-state carry expected."
    )


def test_sessions_carry_slot_provenance():
    """Every menu-generated session names its authoring Slot (R-SESS-024)."""
    sessions = _ironman_competitive_24wk_sessions()
    assert sessions
    assert all(s.slot_name for s in sessions)
    assert any(s.slot_name == "base_sun_long_run" for s in sessions)


_BRICK_KEYS = frozenset(
    {
        ActivityKey.RUN_BRICK_SHORT,
        ActivityKey.RUN_BRICK_LONG,
        ActivityKey.RUN_BRICK_HIM_TEMPO,
    }
)


@pytest.mark.parametrize("ambition", list(AmbitionLevel))
def test_half_ironman_peak_brick_escalates_over_build(ambition):
    """The half-IM PEAK brick must not regress on the late-BUILD brick.

    docs/research/triathlon.md, Brick Sessions periodises bricks BASE
    (short transition runs) -> BUILD (20-40 min at progressive intensity)
    -> PEAK (race simulation), and docs/research/triathlon_long_course.md,
    Key Session Types by Phase puts the half's PEAK bricks "at
    half-Ironman race pace". A PEAK brick escalates over its BUILD one.
    """
    sessions, phase_of = _half_ironman_sessions(ambition)
    bricks = [s for s in sessions if s.activity_key in _BRICK_KEYS]
    build = [s for s in bricks if phase_of[s.week] is PhaseType.BUILD]
    peak = [s for s in bricks if phase_of[s.week] is PhaseType.PEAK]
    assert build, f"{ambition.value}: expected BUILD bricks"
    assert peak, f"{ambition.value}: expected PEAK bricks"

    ensure_registry_loaded()
    assert all(
        get_activity(s.activity_key).family is ActivityFamily.TEMPO for s in peak
    ), (
        f"{ambition.value}: every PEAK brick must be a race-pace (TEMPO) "
        f"brick, got "
        f"{sorted({get_activity(s.activity_key).family.value for s in peak})}"
    )

    longest_build = max(s.target_hours for s in build)
    longest_peak = max(s.target_hours for s in peak)
    assert longest_peak > longest_build + 1e-6, (
        f"{ambition.value}: longest PEAK brick ({longest_peak * 60:.0f} min) "
        f"does not exceed the longest BUILD brick "
        f"({longest_build * 60:.0f} min) — the PEAK brick is the race "
        "rehearsal and must escalate, not regress."
    )


@pytest.mark.parametrize("ambition", list(AmbitionLevel))
def test_half_ironman_bike_threshold_dose_progresses(ambition):
    """Half-IM BIKE_THRESHOLD work per rep must grow BASE -> BUILD -> PEAK.

    ``BIKE_THRESHOLD`` is a flexible-rep shape whose catalog floor is the
    2x8 min pattern (a 49-minute card). docs/research/triathlon_long_course.md,
    Durability — The Central Long-Course Quality makes threshold fitness
    the half's primary performance determinant, and its Key Session Types
    by Phase section names "2x20 min threshold on the bike" as the 70.3
    differentiator.
    """
    sessions, phase_of = _half_ironman_sessions(ambition)
    rides = [s for s in sessions if s.activity_key is ActivityKey.BIKE_THRESHOLD]
    assert rides, f"{ambition.value}: expected BIKE_THRESHOLD sessions"

    by_phase: dict[PhaseType, list[float]] = {}
    for s in rides:
        by_phase.setdefault(phase_of[s.week], []).append(_rep_work_minutes(s))
    ladder = [PhaseType.BASE, PhaseType.BUILD, PhaseType.PEAK]
    assert all(p in by_phase for p in ladder), (
        f"{ambition.value}: expected a threshold ride in BASE, BUILD and PEAK, "
        f"got {sorted(p.value for p in by_phase)}"
    )
    peaks = [max(by_phase[p]) for p in ladder]
    assert peaks[0] < peaks[1] < peaks[2], (
        f"{ambition.value}: rep work per phase reads "
        f"{[round(v, 1) for v in peaks]} min — BASE < BUILD < PEAK expected."
    )

    for s in rides:
        scheduled = s.target_hours * 60.0
        realized = _realized_minutes(s)
        assert abs(realized - scheduled) <= _ROUNDING_HEADROOM_MIN, (
            f"{ambition.value} wk{s.week} {s.slot_name}: prescribed "
            f"{scheduled:.1f} min renders a {realized:.1f} min card — bound "
            "the slot onto the shape's rep lattice (R-SESS-B-008)."
        )
