"""Planner — deterministic session-menu training-plan engine.

Public API:

    from endury.planner import generate_plan, PlanRequest, PlanResult

``generate_plan`` takes a :class:`PlanRequest` and runs the full four-layer
pipeline (phase planner → session generator → macro QP → day assigner),
returning a :class:`PlanResult`. The layer subpackages (``foundations``,
``phase``, ``sessions``, ``macro``, ``days``, ``activity``) hold the
implementation.
"""

from endury.planner.foundations.request import (
    AthleteState,
    PlanRequest,
    SchedulePreferences,
    SolverOptions,
    SportState,
)
from endury.planner.foundations.types import (
    ActivityAssignment,
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PlacedSession,
    RaceGoal,
    SportType,
    TestProtocol,
)
from endury.planner.pipeline import PlanResult, generate_plan

__all__ = [
    "ActivityAssignment",
    "AmbitionLevel",
    "AthleteState",
    "GoalType",
    "PhasePlan",
    "PlacedSession",
    "PlanRequest",
    "PlanResult",
    "RaceGoal",
    "SchedulePreferences",
    "SolverOptions",
    "SportState",
    "SportType",
    "TestProtocol",
    "generate_plan",
]
