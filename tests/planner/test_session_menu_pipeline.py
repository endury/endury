"""End-to-end smoke tests for the session-menu pipeline.

Covers the full ``generate_plan()`` path for Ironman and Sprint triathlon
scenarios. Running-only goals are not tested here.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta

import pytest

from endury.planner import (
    AthleteState,
    PlanRequest,
    SportState,
    generate_plan,
)
from endury.planner.activity.registry import get_activity
from endury.planner.foundations.ambition_tables import get_ambition_profile
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    RaceGoal,
    SportType,
    WeekMode,
)
from endury.planner.macro.capacity import (
    MAX_CAPACITY_CEILING_HOURS,
    build_capacity_projection,
)
from endury.planner.macro.macro_planner import _ROUND_STEP_HOURS
from endury.planner.pipeline import default_profile_for

_START = date(2026, 6, 1)

_RECENT = {
    SportType.RUNNING: 4.0,
    SportType.CYCLING: 6.0,
    SportType.SWIMMING: 2.0,
}
_LONGEST = {
    (SportType.RUNNING, ActivityFamily.LONG): 60,
    (SportType.CYCLING, ActivityFamily.LONG): 90,
    (SportType.SWIMMING, ActivityFamily.EASY): 30,
}

_IRONMAN_GOALS = [
    RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=22, days=-1), "A")
]
_SPRINT_GOALS = [
    RaceGoal(GoalType.SPRINT_TRIATHLON, _START + timedelta(weeks=10, days=-1), "A")
]


def _athlete() -> AthleteState:
    """Build an AthleteState from the module's _RECENT / _LONGEST fixtures."""
    longest_by_sport: dict[SportType, dict[ActivityFamily, int]] = {}
    for (sport, family), minutes in _LONGEST.items():
        longest_by_sport.setdefault(sport, {})[family] = minutes
    return AthleteState(
        sports={
            sport: SportState(
                recent_weekly_hours=hours,
                longest_recent_min=longest_by_sport.get(sport, {}),
            )
            for sport, hours in _RECENT.items()
        }
    )


def _run(goals: list[RaceGoal]):
    result = generate_plan(
        PlanRequest(
            goals=goals,
            training_start=_START,
            ambition=AmbitionLevel.MODERATE,
            athlete=_athlete(),
        )
    )
    return result.phase_plan, result.sessions, result.assignments


@pytest.mark.parametrize(
    ("goals", "label"),
    [(_IRONMAN_GOALS, "ironman_moderate"), (_SPRINT_GOALS, "sprint_tri_moderate")],
)
def test_pipeline_produces_sessions(goals, label) -> None:
    """Pipeline returns non-zero sessions for each triathlon goal type."""
    plan, sessions, _assignments = _run(goals)
    assert len(sessions) > 0, f"{label}: pipeline returned no sessions"
    assert len(plan.weeks) > 0, f"{label}: pipeline returned no weeks"


@pytest.mark.parametrize(
    "goals",
    [_IRONMAN_GOALS, _SPRINT_GOALS],
    ids=["ironman", "sprint"],
)
def test_race_week_has_taper_level_sessions(goals) -> None:
    """RACE weeks get light taper-level sessions around the race day."""
    plan, sessions, assignments = _run(goals)
    race_weeks = {ws.week for ws in plan.weeks if ws.mode == WeekMode.RACE}
    race_sessions = [s for s in sessions if s.week in race_weeks]
    # Race weeks should have some sessions (taper-level pre-race work).
    assert len(race_sessions) > 0, "Race week should have taper sessions"
    # Total hours in race week should be modest (< 6h of training).
    race_hours = sum(s.target_hours for s in race_sessions)
    assert race_hours < 6.0, f"Race week training too heavy: {race_hours:.1f}h"
    # No assignments on race day itself.
    for re in plan.race_events:
        race_date = _START + timedelta(weeks=re.week, days=re.day_of_week)
        race_day_assignments = [a for a in assignments if a.date == race_date]
        assert not race_day_assignments, f"Training scheduled on race day {race_date}"


def test_ironman_weekly_hours_within_capacity() -> None:
    """No week's sized hours exceed the capacity ceiling by more than rounding.

    The QP holds the ceiling; the 5-minute rounding of solved hours after it
    (``macro_planner._round_hours``) may add up to half a step a session.
    """
    plan, sessions, _ = _run(_IRONMAN_GOALS)
    amb = get_ambition_profile(AmbitionLevel.MODERATE)
    capacity = build_capacity_projection(
        _RECENT,
        plan.weeks,
        hours_ramp_limit=amb.hours_ramp_limit,
        max_weekly_hours_ceiling=amb.max_weekly_hours_ceiling,
        recovery_ceiling_factor=amb.recovery_load_fraction,
    )
    week_hours: dict[int, float] = {}
    week_sessions: dict[int, int] = {}
    for s in sessions:
        week_hours[s.week] = week_hours.get(s.week, 0.0) + s.target_hours
        week_sessions[s.week] = week_sessions.get(s.week, 0) + 1
    for week_idx, total in week_hours.items():
        ceil = capacity.weekly_ceiling.get(week_idx, MAX_CAPACITY_CEILING_HOURS)
        rounding = week_sessions[week_idx] * _ROUND_STEP_HOURS / 2
        assert total <= ceil + rounding + 1e-3, (
            f"Week {week_idx}: {total:.2f}h exceeds capacity {ceil:.2f}h"
        )


@pytest.mark.parametrize("weeks", [24, 28, 36], ids=["24wk", "28wk", "36wk"])
def test_low_volume_taper_decreases_monotonically(weeks: int) -> None:
    """A low-volume athlete's taper must step down, never back up.

    At ~1.5 h/wk the capacity ceiling binds every taper week, so the
    ceiling chain -- not the menus -- decides the hours. 24 wk and 36 wk
    BASIC place a PEAK RECOVERY week immediately before the taper, so the
    taper must chain off the un-shed LOAD ceiling rather than the recovery
    week to keep this ordering monotonic.
    """
    recent = {
        SportType.RUNNING: 0.6,
        SportType.CYCLING: 0.6,
        SportType.SWIMMING: 0.3,
    }
    athlete = AthleteState(
        sports={
            sport: SportState(
                recent_weekly_hours=hours,
                longest_recent_min={ActivityFamily.LONG: 30},
            )
            for sport, hours in recent.items()
        }
    )
    result = generate_plan(
        PlanRequest(
            goals=[
                RaceGoal(
                    GoalType.IRONMAN, _START + timedelta(weeks=weeks, days=-1), "A"
                )
            ],
            training_start=_START,
            ambition=AmbitionLevel.BASIC,
            athlete=athlete,
        )
    )
    # The hours each week books; optional sessions (R-SESS-037) ride along.
    week_hours: dict[int, float] = {}
    for s in result.sessions:
        if s.optional_reason is None:
            week_hours[s.week] = week_hours.get(s.week, 0.0) + s.target_hours

    by_week = {ws.week: ws for ws in result.phase_plan.weeks}
    taper_weeks = sorted(k for k, ws in by_week.items() if ws.mode == WeekMode.TAPER)
    race_weeks = sorted(k for k, ws in by_week.items() if ws.mode == WeekMode.RACE)
    assert taper_weeks
    assert race_weeks

    for earlier, later in itertools.pairwise(taper_weeks):
        assert week_hours[later] <= week_hours[earlier] + 1e-9, (
            f"{weeks}wk: taper week {later} ({week_hours[later]:.2f}h) exceeds "
            f"week {earlier} ({week_hours[earlier]:.2f}h)"
        )
    race_hours = week_hours[race_weeks[0]]
    last_taper_hours = week_hours[taper_weeks[-1]]
    assert race_hours <= last_taper_hours + 1e-9, (
        f"{weeks}wk: race week ({race_hours:.2f}h) exceeds the last taper "
        f"week ({last_taper_hours:.2f}h)"
    )


def test_ironman_assignments_cover_all_sessions() -> None:
    """Every session should appear in the day-assigned output."""
    _plan, sessions, assignments = _run(_IRONMAN_GOALS)
    assigned_slots = {a.slot_id for a in assignments}
    session_slots = {s.slot_id for s in sessions}
    missing = session_slots - assigned_slots
    assert not missing, f"Sessions not assigned a day: {missing}"


def test_saturday_race_evicts_end_to_end() -> None:
    """A non-Sunday A-race exercises the race_events wiring end-to-end.

    Race menus assume a Sunday race, so a Saturday Ironman collides
    with the authored Sat shakeout: through generate_plan the collider
    must be evicted (not dropped wholesale) and nothing may land on or
    after the race day. Guards the pipeline → assign_days race_events
    plumbing that Sunday-race tests never exercise.
    """
    race_date = _START + timedelta(weeks=22, days=5)  # Saturday
    assert race_date.weekday() == 5
    goals = [RaceGoal(GoalType.IRONMAN, race_date, "A")]
    _plan, _sessions, assignments = _run(goals)

    on_race = [a for a in assignments if a.date == race_date]
    after_race = [a for a in assignments if a.date > race_date]
    assert not on_race, f"Training scheduled on race day: {on_race}"
    assert not after_race, f"Training scheduled after the A-race: {after_race}"

    # The race week still trains (the Sat collider moved, most sessions kept).
    race_week = (race_date - _START).days // 7
    race_week_dates = {
        a.date for a in assignments if (a.date - _START).days // 7 == race_week
    }
    assert race_week_dates, "Race week lost all its taper sessions"
    per_day: dict = {}
    for a in assignments:
        per_day[a.date] = per_day.get(a.date, 0) + 1
    assert max(per_day.values()) <= 2, "Eviction stacked more than 2 sessions on a day"


def test_half_right_before_the_ironman_peak_keeps_the_big_weekends() -> None:
    """The week after the half has no hard run or ride; the peak keeps two long runs.

    The menu's windows count the weeks of the peak, so an easier week left on
    the peak's menus would take the first long-run weekend (R-PHASE-010).
    """
    plan, sessions, _ = _run(
        [
            RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=29, days=-1), "A"),
            RaceGoal(GoalType.HALF_IRONMAN, _START + timedelta(weeks=21, days=-1), "B"),
        ]
    )
    half = next(e.week for e in plan.race_events if e.goal.priority == "B")
    assert plan.weeks[half + 1].mode == WeekMode.RACE_RECOVERY
    after_half = [s for s in sessions if s.week == half + 1]
    assert after_half
    assert not [
        s
        for s in after_half
        if s.sport != SportType.SWIMMING
        and get_activity(s.activity_key).hardness.is_hard_physiologically
    ]

    peak = {w.week for w in plan.weeks if w.phase == PhaseType.PEAK}
    long_runs = {
        s.week
        for s in sessions
        if s.week in peak
        and s.sport == SportType.RUNNING
        and get_activity(s.activity_key).family == ActivityFamily.LONG
    }
    assert len(long_runs) == 2


def test_sprint_basic_keeps_neuromuscular_run_work() -> None:
    """Sprint BASIC plans materialize the weekly fast-running touch.

    ``run_speed`` cannot build below 48 min, which exceeds BASIC's slot
    bounds and would floor-drop every instance (R-SESS-030), losing all
    speed/neuromuscular work. BASIC fires ``RUN_STRIDES`` at a buildable
    (30, 36) on those slots instead.
    """
    goal = GoalType.SPRINT_TRIATHLON
    result = generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal, _START + timedelta(weeks=10, days=-1), "A")],
            training_start=_START,
            ambition=AmbitionLevel.BASIC,
            athlete=default_profile_for(goal, AmbitionLevel.BASIC),
        )
    )
    load_weeks = {w.week for w in result.phase_plan.weeks if w.mode == WeekMode.LOAD}
    by_slot = {s.slot_id: s for s in result.sessions}
    strides_weeks = {
        by_slot[a.slot_id].week
        for a in result.assignments
        if a.activity_key is ActivityKey.RUN_STRIDES
        and by_slot[a.slot_id].week in load_weeks
    }
    assert len(strides_weeks) >= len(load_weeks) - 1, (
        f"Sprint BASIC lost its neuromuscular run work: strides materialized "
        f"in {sorted(strides_weeks)} of load weeks {sorted(load_weeks)}"
    )
