"""Composition root for the planner session-menu pipeline.

Wires the four deterministic layers together:

``phase_planner`` → ``session_generator`` (+ test injection) →
``macro_planner`` (QP) → ``day_assigner`` (menu-authored days +
race-day eviction).

:func:`generate_plan` is the single public entry point: it takes a
:class:`~endury.planner.foundations.request.PlanRequest`, unpacks the
per-sport :class:`AthleteState` (and schedule/solver bundles) into the dicts
the internal layers expect, and returns a :class:`PlanResult`. Sensible
per-goal defaults live here (``default_profile_for``) so the pipeline is
callable with only a list of goals.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from endury.planner.days.day_assigner import assign_days
from endury.planner.foundations.ambition_tables import get_ambition_profile
from endury.planner.foundations.maintenance_tables import (
    maintenance_weekly_hours,
)
from endury.planner.foundations.request import (
    AthleteState,
    PlanRequest,
    SchedulePreferences,
    SportState,
)
from endury.planner.foundations.types import (
    ActivityAssignment,
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PhaseRequest,
    PhaseType,
    PlacedSession,
    SportType,
    WeekMode,
    is_maintenance_goal,
)
from endury.planner.macro.capacity import build_capacity_projection
from endury.planner.macro.macro_planner import (
    MacroRequest,
    plan_macro,
    skip_macro,
)
from endury.planner.macro.session_pricing import session_tss_per_hour
from endury.planner.macro.tid_tables import get_tid_fractions
from endury.planner.macro.tss_estimator import tss_per_hour
from endury.planner.phase.phase_planner import plan_phases, validate_phase_request
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.sessions.session_generator import generate_session_menu
from endury.planner.sessions.test_injection import inject_test_sessions


def seed_tss_per_hour(
    goal_type: GoalType, priced: Iterable[tuple[float, float]]
) -> float:
    """Return the TSS/hour a plan's opening week realises.

    CTL/ATL seeded from recent weekly hours must speak the same hours→TSS
    language as the macro QP's own pricing, or the athlete starts at a
    fitness their own plan cannot reproduce and week one reads as
    detraining. The QP prices each session by its own activity, so the seed
    is the hours-weighted mean of the opening week's session prices. The
    week's TID bridge (``tid_tables`` → ``tss_estimator``) is only the
    fallback for a session-less opening week. It is deliberately
    sport-blind: TSS is normalised to each sport's own threshold.

    Args:
        goal_type: The A-goal type the plan is built for (selects the
            fallback bridge).
        priced: ``(tss_per_hour, hours)`` pairs of the opening week's
            sessions.

    Returns:
        TSS generated per training hour in the plan's first week.
    """
    tss = 0.0
    hours = 0.0
    for price, h in priced:
        tss += price * h
        hours += h
    if hours > 0:
        return tss / hours
    phase = PhaseType.MAINTAIN if is_maintenance_goal(goal_type) else PhaseType.BASE
    return tss_per_hour(get_tid_fractions(phase, WeekMode.LOAD, goal_type))


def initial_load_state(
    athlete: AthleteState,
    goal_type: GoalType,
    opening_week: Iterable[tuple[ActivityKey, float]],
) -> tuple[dict[SportType, float], dict[SportType, float]]:
    """Return per-sport (ctl_0, atl_0) seeds in weekly-TSS space.

    Measured values from :class:`SportState` win; otherwise CTL is derived
    from recent weekly hours via :func:`seed_tss_per_hour` over the opening
    week's session prices and ATL falls back to CTL. Used by the pipeline,
    the scorecard, the projections builder and the benchmark so the forecast
    starts from the one derivation the macro QP plans from.

    Args:
        athlete: Per-sport athlete state (post-defaults).
        goal_type: The A-goal type (fallback bridge for an empty week).
        opening_week: ``(activity_key, hours)`` of the plan's week-0
            sessions.

    Returns:
        Tuple of ``(ctl_0, atl_0)`` dicts keyed by sport.
    """
    seed = seed_tss_per_hour(
        goal_type,
        ((session_tss_per_hour(key, hours), hours) for key, hours in opening_week),
    )
    ctl_0 = {
        s: (st.ctl_0 if st.ctl_0 is not None else st.recent_weekly_hours * seed)
        for s, st in athlete.sports.items()
    }
    atl_0 = {
        s: (st.atl_0 if st.atl_0 is not None else ctl_0[s])
        for s, st in athlete.sports.items()
    }
    return ctl_0, atl_0


#: Default recent weekly training hours per race goal and ambition tier —
#: the volume the athlete is assumed to arrive with when nothing better is
#: known. Recent hours are also the capacity projector's ramp base, so a
#: lower seed shrinks the plan: each cell keeps the ratio of the plan's own
#: peak week to this seed within the 1.36-2.25x band (geometric midpoint ~1.75x), and
#: the table stays monotone in
#: both distance and tier. A seed above the tier's own peak would build a
#: plan that detrains the athlete on paper.
DEFAULT_RECENT_WEEKLY_HOURS: dict[GoalType, dict[AmbitionLevel, float]] = {
    GoalType.RUNNING_5K: {
        AmbitionLevel.BASIC: 1.35,
        AmbitionLevel.MODERATE: 1.50,
        AmbitionLevel.COMPETITIVE: 1.60,
    },
    GoalType.RUNNING_10K: {
        AmbitionLevel.BASIC: 1.85,
        AmbitionLevel.MODERATE: 2.20,
        AmbitionLevel.COMPETITIVE: 2.40,
    },
    GoalType.RUNNING_HALF_MARATHON: {
        AmbitionLevel.BASIC: 2.30,
        AmbitionLevel.MODERATE: 2.50,
        AmbitionLevel.COMPETITIVE: 2.90,
    },
    GoalType.RUNNING_MARATHON: {
        AmbitionLevel.BASIC: 2.90,
        AmbitionLevel.MODERATE: 3.00,
        AmbitionLevel.COMPETITIVE: 3.50,
    },
    # Trail seeds follow the same band and monotonicity rule as
    # the goals above. The 20K tier is an extrapolation below the 50K row
    # (docs/research/trail_running.md, Peak weekly hours by tier) rather
    # than a directly sourced value, so its ratio is held close to the
    # band's midpoint rather than tuned further.
    GoalType.TRAIL_20K: {
        AmbitionLevel.BASIC: 2.50,
        AmbitionLevel.MODERATE: 3.90,
        AmbitionLevel.COMPETITIVE: 4.55,
    },
    GoalType.TRAIL_50K: {
        AmbitionLevel.BASIC: 3.60,
        AmbitionLevel.MODERATE: 4.75,
        AmbitionLevel.COMPETITIVE: 5.35,
    },
    GoalType.TRAIL_100K: {
        AmbitionLevel.BASIC: 5.60,
        AmbitionLevel.MODERATE: 6.80,
        AmbitionLevel.COMPETITIVE: 7.50,
    },
    GoalType.SPRINT_TRIATHLON: {
        AmbitionLevel.BASIC: 3.20,
        AmbitionLevel.MODERATE: 3.70,
        AmbitionLevel.COMPETITIVE: 3.90,
    },
    GoalType.OLYMPIC_TRIATHLON: {
        AmbitionLevel.BASIC: 4.00,
        AmbitionLevel.MODERATE: 4.50,
        AmbitionLevel.COMPETITIVE: 5.25,
    },
    GoalType.HALF_IRONMAN: {
        AmbitionLevel.BASIC: 7.25,
        AmbitionLevel.MODERATE: 7.50,
        AmbitionLevel.COMPETITIVE: 8.75,
    },
    GoalType.IRONMAN: {
        AmbitionLevel.BASIC: 7.50,
        AmbitionLevel.MODERATE: 8.00,
        AmbitionLevel.COMPETITIVE: 9.25,
    },
}

#: Longest recent session per sport, keyed by the family each sport's
#: athlete source reports it under (``intervals.athlete_source._LONGEST_FAMILY``).
#: Not tier-scaled — it seeds the progressive ramp's opening duration, which
#: the session generator floors at ``_PROGRESSIVE_FLOOR`` (run 75 / bike 90 /
#: swim 30). These defaults sit at or below that floor, so every
#: default-profile plan stays bit-identical; only a measured athlete above
#: the floor opens higher.
_DEFAULT_LONGEST_RECENT_MIN: dict[SportType, dict[ActivityFamily, int]] = {
    SportType.RUNNING: {ActivityFamily.LONG: 60},
    SportType.CYCLING: {ActivityFamily.LONG: 90},
    SportType.SWIMMING: {ActivityFamily.EASY: 30},
}

#: Emission order of the sports map (the goal's own split is keyed
#: swim/bike/run; callers and serializers read run/bike/swim).
_SPORT_ORDER: tuple[SportType, ...] = (
    SportType.RUNNING,
    SportType.CYCLING,
    SportType.SWIMMING,
)


def default_recent_weekly_hours(
    goal_type: GoalType,
    ambition: AmbitionLevel,
) -> float:
    """Return the default total recent weekly hours for a goal and tier.

    Maintenance goals converge on their tier's target volume rather than
    ramping toward a race, so they seed *at* that volume: a flat plan from
    week one is the intended outcome, and the peak/seed band race goals are
    held to does not apply.

    Args:
        goal_type: The A-goal type the plan is built for.
        ambition: The plan's ambition tier.

    Returns:
        Total assumed recent weekly training hours across all goal sports.
    """
    if is_maintenance_goal(goal_type):
        return maintenance_weekly_hours(goal_type, ambition)
    return DEFAULT_RECENT_WEEKLY_HOURS[goal_type][ambition]


def default_profile_for(
    goal_type: GoalType,
    ambition: AmbitionLevel = AmbitionLevel.MODERATE,
) -> AthleteState:
    """Return a sensible default :class:`AthleteState` for a goal type.

    Single source of the goal-default athlete: a runner gets one
    ``RUNNING`` sport; a triathlete gets run/bike/swim. The keys of the
    returned ``sports`` map define the plan's sport set. The total volume
    comes from :func:`default_recent_weekly_hours` and is split across the
    sports by the goal's own ``default_sport_split``, so the assumed
    athlete arrives with the balance their plan will prescribe.

    Args:
        goal_type: The A-goal type the plan is built for.
        ambition: The plan's ambition tier. Defaults to ``MODERATE`` for
            the callers that only need the sport set (the intervals.icu
            resolver, the goal catalog).

    Returns:
        Default per-sport athlete state.
    """
    total = default_recent_weekly_hours(goal_type, ambition)
    split = GOAL_PROFILES[goal_type].default_sport_split
    return AthleteState(
        sports={
            sport: SportState(
                recent_weekly_hours=total * split[sport],
                longest_recent_min=dict(_DEFAULT_LONGEST_RECENT_MIN[sport]),
            )
            for sport in _SPORT_ORDER
            if sport in split
        }
    )


# Public result type


@dataclass(frozen=True, slots=True)
class PlanResult:
    """Public output of :func:`generate_plan`.

    Attributes:
        phase_plan: Week-by-week phase/mode skeleton from L1.
        sessions: QP-sized sessions with final target hours.
        assignments: Calendar-day assignments from the day assigner.
        warnings: L1 soft-validation warnings (plan is valid but
            non-ideal, e.g. horizon beyond the goal's focused-prep
            ceiling) — user-facing sentences.
        session_tss_per_hour: slot_id → the TSS/h coefficient the QP sized
            that session with, at its menu target hours.
    """

    phase_plan: PhasePlan
    sessions: list[PlacedSession]
    assignments: list[ActivityAssignment]
    warnings: tuple[str, ...] = ()
    session_tss_per_hour: dict[str, float] = field(default_factory=dict)


# Pipeline entry point


def generate_plan(request: PlanRequest) -> PlanResult:
    """Generate a complete training plan from a :class:`PlanRequest`.

    Runs the session-menu pipeline (L1 phase plan → session generation →
    macro QP → day assignment), unpacking the request's per-sport
    :class:`AthleteState` and schedule/solver bundles into the inputs each
    layer expects. When ``request.athlete`` is ``None``, defaults are
    derived from the goal via :func:`default_profile_for`.

    Args:
        request: The plan request (goals, training start, ambition, athlete
            state, schedule preferences, solver options).

    Returns:
        A :class:`PlanResult` bundling the phase plan, QP-sized sessions, and
        day-assigned activities.

    Raises:
        ValueError: If ``training_start`` is not a Monday (the day assigner
            maps menu-authored weekdays from a Monday week 0), or if the
            phase request fails hard validation.
    """
    if request.training_start.weekday() != 0:
        raise ValueError(
            f"training_start must be a Monday, "
            f"got {request.training_start.isoformat()} "
            f"({request.training_start.strftime('%A')})"
        )
    goal_type = request.goals[0].goal_type

    # Athlete state: provided, or goal-default with sport set = its keys.
    athlete = request.athlete or default_profile_for(goal_type, request.ambition)
    recent_hours = {s: st.recent_weekly_hours for s, st in athlete.sports.items()}
    longest_recent = {
        (sport, family): minutes
        for sport, st in athlete.sports.items()
        for family, minutes in st.longest_recent_min.items()
    }
    swim = athlete.sports.get(SportType.SWIMMING)
    swim_css_sec_per_100m = swim.css_sec_per_100m if swim is not None else None

    # Schedule and solver options.
    schedule = request.schedule or SchedulePreferences()
    solver = request.solver
    amb = get_ambition_profile(request.ambition)

    # Pipeline layers.
    phase_request = PhaseRequest(
        goals=request.goals,
        training_start=request.training_start,
        ambition=request.ambition,
        prior_training_weeks=request.prior_training_weeks,
        horizon_join_weeks=request.horizon_join_weeks,
    )
    plan = plan_phases(phase_request)
    # plan_phases raises on hard errors; keep the soft warnings so callers
    # can surface them.
    plan_warnings = tuple(validate_phase_request(phase_request).warnings)

    # Maintenance plans converge on the ambition tier's maintenance
    # volume instead of the race-prep peak ceiling: below the tier the
    # adaptive ramp climbs to it, at/above it the ceiling binds flat
    # from week one.
    max_ceiling = (
        maintenance_weekly_hours(goal_type, request.ambition)
        if is_maintenance_goal(goal_type)
        else amb.max_weekly_hours_ceiling
    )
    capacity = build_capacity_projection(
        recent_hours,
        plan.weeks,
        hours_ramp_limit=amb.hours_ramp_limit,
        max_weekly_hours_ceiling=max_ceiling,
        recovery_ceiling_factor=amb.recovery_load_fraction,
    )

    sessions = generate_session_menu(
        plan,
        longest_recent,
        ambition=request.ambition,
        capacity=capacity,
        swim_css_sec_per_100m=swim_css_sec_per_100m,
        weekly_hours_cap=request.constraints.weekly_hours_cap,
    )
    sessions = inject_test_sessions(
        plan, sessions, schedule.test_protocol, capacity=capacity
    )
    ctl_0, atl_0 = initial_load_state(
        athlete,
        goal_type,
        [(s.activity_key, s.target_hours) for s in sessions if s.week == 0],
    )

    if solver.use_macro_solver:
        budget = plan_macro(
            MacroRequest(
                phase_plan=plan,
                sessions=sessions,
                ctl_0=ctl_0,
                atl_0=atl_0,
                capacity=capacity,
                weekly_hours_cap=request.constraints.weekly_hours_cap,
            )
        )
    else:
        budget = skip_macro(sessions)

    assignments = assign_days(budget.sessions, request.training_start, plan.race_events)

    return PlanResult(
        phase_plan=plan,
        sessions=budget.sessions,
        assignments=assignments,
        warnings=plan_warnings,
        session_tss_per_hour=budget.session_tss_per_hour,
    )
