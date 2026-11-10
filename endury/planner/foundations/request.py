"""Request-shaped input contracts for the planner pipeline.

Bundles a plan request into self-consistent frozen dataclasses:

* :class:`SportState` / :class:`AthleteState` — per-sport fitness state,
  where the *set of sports* the plan touches falls out of
  ``AthleteState.sports.keys()`` instead of being inferred.
* :class:`SchedulePreferences` — fitness-test schedule.
* :class:`SolverOptions` — macro solver toggle.
* :class:`PlanningConstraints` — athlete-authored limits on the plan.
* :class:`PlanRequest` — the single argument to ``generate_plan``.

Part of the foundations layer: it imports only from ``foundations.types``,
and every contract here is ``frozen=True, slots=True``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    RaceGoal,
    SportType,
    TestProtocol,
)

#: Default plan start date (a Monday) used when a request omits one.
DEFAULT_START: date = date(2026, 6, 1)


@dataclass(frozen=True, slots=True)
class SportState:
    """Everything the planner needs about ONE sport for ONE athlete.

    Attributes:
        recent_weekly_hours: Recent average weekly training hours in this sport.
        longest_recent_min: Longest recent session (minutes) per activity family.
        ctl_0: Initial chronic training load. ``None`` → derived from hours.
        atl_0: Initial acute training load. ``None`` → falls back to ``ctl_0``.
        css_sec_per_100m: Critical swim speed (swim only; metre→time conversion).
        ftp_watts: Functional threshold power (bike; staged for rendering).
        threshold_pace_sec_per_km: Run threshold pace (staged for rendering).
    """

    recent_weekly_hours: float
    longest_recent_min: dict[ActivityFamily, int] = field(default_factory=dict)
    ctl_0: float | None = None
    atl_0: float | None = None
    css_sec_per_100m: float | None = None
    ftp_watts: float | None = None
    threshold_pace_sec_per_km: float | None = None


@dataclass(frozen=True, slots=True)
class AthleteState:
    """Per-sport fitness state. The keys of ``sports`` define the sport set.

    Attributes:
        sports: Map of sport → :class:`SportState`. The presence of a sport
            key is what makes the plan touch that sport.
    """

    sports: dict[SportType, SportState] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SchedulePreferences:
    """Scheduling inputs for test injection.

    Attributes:
        test_protocol: Optional fitness-test schedule. ``None`` → menu tests only.
    """

    test_protocol: TestProtocol | None = None


@dataclass(frozen=True, slots=True)
class SolverOptions:
    """Solver toggles for the pipeline.

    Attributes:
        use_macro_solver: Run the cvxpy QP macro planner (else menu targets).
    """

    use_macro_solver: bool = True


@dataclass(frozen=True, slots=True)
class PlanningConstraints:
    """Limits the athlete asked for, on top of the goal's own policy.

    Attributes:
        weekly_hours_cap: Total weekly training hours the athlete wants to
            stay under. A SOFT target: the macro QP is penalised per hour
            above it on LOAD/RECOVERY weeks, so the number beats the menu's
            prescription but never a session's own floor. ``None`` → no cap
            (today's behaviour).
    """

    weekly_hours_cap: float | None = None


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """The single input to ``generate_plan``.

    Attributes:
        goals: Race goals (at least one A-goal).
        training_start: Plan start date (must be a Monday).
        ambition: Training ambition level.
        athlete: Per-sport athlete state. ``None`` → defaults derived from goals.
        schedule: Day/test scheduling preferences. ``None`` → goal defaults.
        solver: Solver toggles.
        constraints: Athlete-authored limits (soft weekly hours cap).
        prior_training_weeks: Weeks already trained toward the same goals
            before ``training_start`` (replanning). The phase skeleton is
            built over the virtual horizon (prior + actual) and the leading
            prior weeks are truncated. ``0`` = fresh plan.
        horizon_join_weeks: Weeks a too-short race horizon lacks, built
            virtually before the trained ones and discarded (never
            trained, so the plan still opens on its first real week).
    """

    goals: list[RaceGoal]
    training_start: date = DEFAULT_START
    ambition: AmbitionLevel = AmbitionLevel.COMPETITIVE
    athlete: AthleteState | None = None
    schedule: SchedulePreferences | None = None
    solver: SolverOptions = field(default_factory=SolverOptions)
    constraints: PlanningConstraints = field(default_factory=PlanningConstraints)
    prior_training_weeks: int = 0
    horizon_join_weeks: int = 0
