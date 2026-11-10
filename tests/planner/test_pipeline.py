"""Tests for the planner composition root (``endury.planner.pipeline``).

Covers the public ``generate_plan(PlanRequest)`` entry point: the
default-profile factory, the athlete/schedule/solver unpacking branch matrix,
and goal-type helpers, so the relocated composition root is fully exercised
under the coverage gate.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from endury.planner import (
    AthleteState,
    PlanRequest,
    PlanResult,
    SchedulePreferences,
    SolverOptions,
    SportState,
    generate_plan,
)
from endury.planner import pipeline as pipeline_module
from endury.planner.foundations.maintenance_tables import maintenance_weekly_hours
from endury.planner.foundations.types import (
    ActivityAssignment,
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PhaseType,
    PlacedSession,
    RaceGoal,
    SportType,
    WeekMode,
    is_maintenance_goal,
)
from endury.planner.macro.macro_config import CTL_DECAY_WEEKLY
from endury.planner.macro.macro_planner import MacroBudget, MacroRequest, plan_macro
from endury.planner.macro.session_pricing import session_tss_per_hour
from endury.planner.macro.tid_tables import get_tid_fractions
from endury.planner.macro.tss_estimator import tss_per_hour
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import (
    DEFAULT_RECENT_WEEKLY_HOURS,
    default_profile_for,
    initial_load_state,
    seed_tss_per_hour,
)

_START = date(2026, 6, 1)
_RUNNING_GOALS = [
    RaceGoal(GoalType.RUNNING_MARATHON, _START + timedelta(weeks=12, days=-1), "A")
]
_TRI_GOALS = [
    RaceGoal(GoalType.SPRINT_TRIATHLON, _START + timedelta(weeks=10, days=-1), "A")
]


def test_generate_plan_returns_plan_result() -> None:
    """Minimal request (goals only) yields a populated PlanResult."""
    result = generate_plan(PlanRequest(goals=_TRI_GOALS, training_start=_START))

    assert isinstance(result, PlanResult)
    assert isinstance(result.phase_plan, PhasePlan)
    assert result.sessions
    assert all(isinstance(s, PlacedSession) for s in result.sessions)
    assert all(isinstance(a, ActivityAssignment) for a in result.assignments)


def test_generate_plan_deterministic() -> None:
    """Two identical requests give byte-identical plans, assignments included.

    Sessions live on their menu-authored days, so the whole pipeline is
    reproducible end-to-end.
    """
    req = PlanRequest(
        goals=_RUNNING_GOALS, training_start=_START, ambition=AmbitionLevel.MODERATE
    )
    a = generate_plan(req)
    b = generate_plan(req)

    assert a.phase_plan == b.phase_plan
    assert a.sessions == b.sessions
    assert a.assignments == b.assignments


def test_generate_plan_carries_phase_warnings() -> None:
    """A fresh long-horizon plan surfaces the L1 soft warning on the result."""
    goals = [RaceGoal(GoalType.RUNNING_5K, _START + timedelta(weeks=20, days=-1), "A")]
    result = generate_plan(PlanRequest(goals=goals, training_start=_START))
    assert any("maintenance block" in w for w in result.warnings)


def test_generate_plan_clean_request_has_no_warnings() -> None:
    result = generate_plan(PlanRequest(goals=_TRI_GOALS, training_start=_START))
    assert result.warnings == ()


def test_running_goal_default_profile() -> None:
    """No athlete supplied → running default profile (one RUNNING sport)."""
    result = generate_plan(PlanRequest(goals=_RUNNING_GOALS, training_start=_START))

    assert len(result.phase_plan.weeks) > 0
    assert result.sessions
    assert result.assignments


def test_triathlon_goal_default_profile() -> None:
    """No athlete supplied → triathlon default profile (three sports)."""
    result = generate_plan(PlanRequest(goals=_TRI_GOALS, training_start=_START))

    assert result.sessions
    assert {s.sport for s in result.sessions} <= {
        SportType.RUNNING,
        SportType.CYCLING,
        SportType.SWIMMING,
    }


def test_explicit_athlete_and_derived_ctl() -> None:
    """Explicit ctl_0/atl_0 on one sport; derived (None) on another."""
    athlete = AthleteState(
        sports={
            SportType.RUNNING: SportState(
                recent_weekly_hours=6.0,
                longest_recent_min={ActivityFamily.LONG: 75},
                ctl_0=25.0,
                atl_0=45.0,
            ),
            SportType.CYCLING: SportState(
                recent_weekly_hours=8.0,
                longest_recent_min={ActivityFamily.LONG: 90},
            ),
        }
    )
    result = generate_plan(
        PlanRequest(goals=_TRI_GOALS, training_start=_START, athlete=athlete)
    )
    assert result.sessions


def test_swim_css_provided() -> None:
    """A swim SportState with css_sec_per_100m exercises the CSS unpack path."""
    athlete = AthleteState(
        sports={
            SportType.RUNNING: SportState(recent_weekly_hours=4.0),
            SportType.CYCLING: SportState(recent_weekly_hours=6.0),
            SportType.SWIMMING: SportState(
                recent_weekly_hours=2.0, css_sec_per_100m=90.0
            ),
        }
    )
    result = generate_plan(
        PlanRequest(goals=_TRI_GOALS, training_start=_START, athlete=athlete)
    )
    assert result.sessions


def test_explicit_schedule_and_solver() -> None:
    """Providing an explicit schedule and solver toggles hits those branches."""
    result = generate_plan(
        PlanRequest(
            goals=_RUNNING_GOALS,
            training_start=_START,
            schedule=SchedulePreferences(),
            solver=SolverOptions(use_macro_solver=True),
        )
    )
    assert result.assignments
    assert len(result.assignments) == len(result.sessions)


def test_skip_macro_solver() -> None:
    """use_macro_solver=False uses menu target hours directly (no QP)."""
    with_qp = generate_plan(PlanRequest(goals=_RUNNING_GOALS, training_start=_START))
    no_qp = generate_plan(
        PlanRequest(
            goals=_RUNNING_GOALS,
            training_start=_START,
            solver=SolverOptions(use_macro_solver=False),
        )
    )
    assert len(no_qp.sessions) == len(with_qp.sessions)


def test_default_profile_for_partitions() -> None:
    """The default factory yields one sport for running, three for tri."""
    assert set(default_profile_for(GoalType.RUNNING_MARATHON).sports) == {
        SportType.RUNNING
    }
    assert set(default_profile_for(GoalType.IRONMAN).sports) == {
        SportType.RUNNING,
        SportType.CYCLING,
        SportType.SWIMMING,
    }


#: Representative prep length per goal — the horizon the peak/seed band is
#: enforced at. Sprint's 8-10 wk cells read 1.28-1.32 instead: a BASIC
#: 7 %/wk ramp over ~5 LOAD weeks cannot reach 1.36x at any seed that also
#: keeps the plan's peak-week volume, so the band is a long-prep property
#: there.
_DEFAULT_PROFILE_HORIZON: dict[GoalType, int] = {
    GoalType.RUNNING_5K: 12,
    GoalType.RUNNING_10K: 12,
    GoalType.RUNNING_HALF_MARATHON: 14,
    GoalType.RUNNING_MARATHON: 18,
    GoalType.SPRINT_TRIATHLON: 12,
    GoalType.OLYMPIC_TRIATHLON: 14,
    GoalType.HALF_IRONMAN: 20,
    GoalType.IRONMAN: 24,
    # Trail cells at their max_recommended_weeks.
    GoalType.TRAIL_20K: 20,
    GoalType.TRAIL_50K: 24,
    GoalType.TRAIL_100K: 28,
}

#: A healthy plan seeds its athlete 1.36-2.25x below its own peak week.
_PEAK_SEED_BAND = (1.36, 2.25)


def _peak_weekly_hours(result: PlanResult) -> float:
    """Return the plan's largest week by summed session target hours.

    An optional session is outside the plan's volume (R-SESS-037).
    """
    weekly: dict[int, float] = {}
    for session in result.sessions:
        if session.optional_reason is not None:
            continue
        weekly[session.week] = weekly.get(session.week, 0.0) + session.target_hours
    return max(weekly.values())


@pytest.mark.parametrize("ambition", list(AmbitionLevel))
@pytest.mark.parametrize("goal_type", list(_DEFAULT_PROFILE_HORIZON))
def test_default_profile_plan_builds_its_own_athlete_up(
    goal_type: GoalType,
    ambition: AmbitionLevel,
) -> None:
    """A default-profile plan must peak above the volume it seeds.

    The seeds are per-distance and per-tier, inside the 1.36-2.25x peak/seed band at
    each goal's representative prep
    length.
    """
    weeks = _DEFAULT_PROFILE_HORIZON[goal_type]
    athlete = default_profile_for(goal_type, ambition)
    seed = sum(state.recent_weekly_hours for state in athlete.sports.values())
    result = generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal_type, _START + timedelta(weeks=weeks, days=-1), "A")],
            training_start=_START,
            ambition=ambition,
        )
    )

    ratio = _peak_weekly_hours(result) / seed
    assert ratio >= 1.0, f"{goal_type.value}/{ambition.value} detrains its own athlete"
    assert _PEAK_SEED_BAND[0] <= ratio <= _PEAK_SEED_BAND[1]


@pytest.mark.parametrize("ambition", list(AmbitionLevel))
@pytest.mark.parametrize(
    "goal_type", [GoalType.RUNNING_MAINTENANCE, GoalType.TRIATHLON_MAINTENANCE]
)
def test_maintenance_default_seeds_at_the_tier_volume(
    goal_type: GoalType,
    ambition: AmbitionLevel,
) -> None:
    """Maintenance goals converge on their seed rather than ramping past it.

    They are exempt from the peak/seed band by design: the block's whole
    purpose is to hold the tier's volume, so seeding *at* it makes the
    capacity ceiling bind flat from week one.
    """
    athlete = default_profile_for(goal_type, ambition)
    seed = sum(state.recent_weekly_hours for state in athlete.sports.values())
    assert seed == pytest.approx(maintenance_weekly_hours(goal_type, ambition))


def test_default_recent_weekly_hours_table_shape() -> None:
    """The seed table covers every race goal and rises with ambition."""
    assert set(DEFAULT_RECENT_WEEKLY_HOURS) == {
        goal for goal in GoalType if not is_maintenance_goal(goal)
    }
    for goal, tiers in DEFAULT_RECENT_WEEKLY_HOURS.items():
        assert set(tiers) == set(AmbitionLevel), goal
        assert (
            tiers[AmbitionLevel.BASIC]
            <= tiers[AmbitionLevel.MODERATE]
            <= tiers[AmbitionLevel.COMPETITIVE]
        ), goal


def test_seed_is_the_hours_weighted_mean_of_the_opening_week() -> None:
    """R-FND-018: the seed speaks the opening week's own session prices."""
    priced = [(40.0, 1.0), (80.0, 0.5), (60.0, 2.5)]
    expected = (40.0 * 1.0 + 80.0 * 0.5 + 60.0 * 2.5) / 4.0
    assert seed_tss_per_hour(GoalType.RUNNING_MARATHON, priced) == pytest.approx(
        expected
    )


@pytest.mark.parametrize("goal_type", list(GoalType))
def test_session_less_opening_week_seeds_from_the_bridge(goal_type: GoalType) -> None:
    """No sessions (or no hours) → the opening week's TID bridge."""
    phase = PhaseType.MAINTAIN if is_maintenance_goal(goal_type) else PhaseType.BASE
    bridge = tss_per_hour(get_tid_fractions(phase, WeekMode.LOAD, goal_type))
    assert seed_tss_per_hour(goal_type, ()) == pytest.approx(bridge)
    assert seed_tss_per_hour(goal_type, [(70.0, 0.0)]) == pytest.approx(bridge)


def test_initial_load_state_prices_the_opening_week_sessions() -> None:
    """Hours-derived CTL is recent hours x the week's mean session price."""
    athlete = AthleteState(
        sports={
            SportType.RUNNING: SportState(recent_weekly_hours=4.0),
            SportType.CYCLING: SportState(recent_weekly_hours=5.0, ctl_0=321.0),
        }
    )
    week = [(ActivityKey.RUN_EASY, 1.0), (ActivityKey.RUN_TEMPO, 0.75)]
    mean = seed_tss_per_hour(
        GoalType.RUNNING_MARATHON,
        [(session_tss_per_hour(k, h), h) for k, h in week],
    )
    ctl_0, atl_0 = initial_load_state(athlete, GoalType.RUNNING_MARATHON, week)
    assert ctl_0[SportType.RUNNING] == pytest.approx(4.0 * mean)
    assert atl_0[SportType.RUNNING] == ctl_0[SportType.RUNNING]
    # Measured values still win.
    assert ctl_0[SportType.CYCLING] == 321.0


def test_plan_result_carries_every_session_price() -> None:
    """``PlanResult.session_tss_per_hour`` prices every sized session."""
    result = generate_plan(PlanRequest(goals=_TRI_GOALS, training_start=_START))
    assert set(result.session_tss_per_hour) == {s.slot_id for s in result.sessions}
    assert all(v > 0 for v in result.session_tss_per_hour.values())


@pytest.mark.parametrize("goal_type", [GoalType.RUNNING_MARATHON, GoalType.IRONMAN])
def test_hours_matched_plan_holds_ctl_flat(
    goal_type: GoalType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A week-0 that delivers the athlete's recent hours leaves CTL unmoved.

    The Banister step is ``CTL' = a·CTL + (1-a)·hours·m``, so a seed of
    ``hours·m`` is a fixed point exactly when the seed coefficient is ``m``:
    the hours-weighted price of the menu's week-0 sessions the QP sees.
    """
    seen: list[MacroRequest] = []

    def _spy(request: MacroRequest) -> MacroBudget:
        seen.append(request)
        return plan_macro(request)

    monkeypatch.setattr(pipeline_module, "plan_macro", _spy)
    weeks = GOAL_PROFILES[goal_type].min_horizon_weeks + 4
    athlete = default_profile_for(goal_type)
    generate_plan(
        PlanRequest(
            goals=[RaceGoal(goal_type, _START + timedelta(weeks=weeks), "A")],
            training_start=_START,
            athlete=athlete,
        )
    )
    (request,) = seen
    week_0 = [s for s in request.sessions if s.week == 0]
    assert week_0
    multiplier = sum(
        session_tss_per_hour(s.activity_key, s.target_hours) * s.target_hours
        for s in week_0
    ) / sum(s.target_hours for s in week_0)

    for sport, state in athlete.sports.items():
        ctl_0 = request.ctl_0[sport]
        week_tss = state.recent_weekly_hours * multiplier
        ctl_1 = CTL_DECAY_WEEKLY * ctl_0 + (1 - CTL_DECAY_WEEKLY) * week_tss
        assert ctl_1 == pytest.approx(ctl_0)


@pytest.mark.parametrize("goal_type", list(GoalType))
def test_min_horizon_weeks_bisects_to_true_qp_feasibility(goal_type: GoalType) -> None:
    """``GOAL_PROFILES[...].min_horizon_weeks`` is exactly QP-feasible.

    Bisected empirically over the deterministic pipeline (default athlete,
    no DB): the literature-derived minimum horizon must run the full
    pipeline — including the macro QP, not just the phase skeleton — for
    every ambition tier. One week shorter must still be rejected by the
    phase planner's own human-readable, goal-named message rather than a
    raw solver status.
    """
    weeks = GOAL_PROFILES[goal_type].min_horizon_weeks
    for ambition in AmbitionLevel:
        result = generate_plan(
            PlanRequest(
                goals=[RaceGoal(goal_type, _START + timedelta(weeks=weeks - 1), "A")],
                training_start=_START,
                ambition=ambition,
            )
        )
        assert result.sessions

        with pytest.raises(ValueError, match=f"at least {weeks} weeks"):
            generate_plan(
                PlanRequest(
                    goals=[
                        RaceGoal(goal_type, _START + timedelta(weeks=weeks - 2), "A")
                    ],
                    training_start=_START,
                    ambition=ambition,
                )
            )
