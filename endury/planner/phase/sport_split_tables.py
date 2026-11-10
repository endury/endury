"""Phase-dependent sport-split lookup tables for the L1 phase planner.

Triathlon goals benefit from phase-varying sport distributions:

* **BASE** — more swimming (technique acquisition while fatigue is low)
  and running (aerobic foundation).
* **BUILD** — cycling emphasis increases (sport-specific endurance, bricks).
* **PEAK** — converge toward race-day demands (minimal swim for long-course).
* **TAPER** — maintain build-phase balance during reduced volume.

Running-only goals are absent — the caller falls back to the goal's
``default_sport_split`` from ``phase_tables.py``.
"""

from __future__ import annotations

from endury.planner.foundations.types import GoalType, PhaseType, SportType

# Phase-keyed sport splits (triathlon goals only).
PHASE_SPORT_SPLITS: dict[GoalType, dict[PhaseType, dict[SportType, float]]] = {
    GoalType.SPRINT_TRIATHLON: {
        PhaseType.BASE: {
            SportType.SWIMMING: 0.23,
            SportType.CYCLING: 0.42,
            SportType.RUNNING: 0.35,
        },
        PhaseType.BUILD: {
            SportType.SWIMMING: 0.20,
            SportType.CYCLING: 0.45,
            SportType.RUNNING: 0.35,
        },
        PhaseType.PEAK: {
            SportType.SWIMMING: 0.18,
            SportType.CYCLING: 0.47,
            SportType.RUNNING: 0.35,
        },
        PhaseType.TAPER: {
            SportType.SWIMMING: 0.20,
            SportType.CYCLING: 0.45,
            SportType.RUNNING: 0.35,
        },
    },
    GoalType.OLYMPIC_TRIATHLON: {
        PhaseType.BASE: {
            SportType.SWIMMING: 0.23,
            SportType.CYCLING: 0.42,
            SportType.RUNNING: 0.35,
        },
        PhaseType.BUILD: {
            SportType.SWIMMING: 0.20,
            SportType.CYCLING: 0.45,
            SportType.RUNNING: 0.35,
        },
        PhaseType.PEAK: {
            SportType.SWIMMING: 0.18,
            SportType.CYCLING: 0.47,
            SportType.RUNNING: 0.35,
        },
        PhaseType.TAPER: {
            SportType.SWIMMING: 0.20,
            SportType.CYCLING: 0.45,
            SportType.RUNNING: 0.35,
        },
    },
    GoalType.HALF_IRONMAN: {
        PhaseType.BASE: {
            SportType.SWIMMING: 0.18,
            SportType.CYCLING: 0.47,
            SportType.RUNNING: 0.35,
        },
        PhaseType.BUILD: {
            SportType.SWIMMING: 0.15,
            SportType.CYCLING: 0.50,
            SportType.RUNNING: 0.35,
        },
        PhaseType.PEAK: {
            SportType.SWIMMING: 0.17,
            SportType.CYCLING: 0.48,
            SportType.RUNNING: 0.35,
        },
        PhaseType.TAPER: {
            SportType.SWIMMING: 0.15,
            SportType.CYCLING: 0.50,
            SportType.RUNNING: 0.35,
        },
    },
    GoalType.IRONMAN: {
        PhaseType.BASE: {
            SportType.SWIMMING: 0.21,
            SportType.CYCLING: 0.52,
            SportType.RUNNING: 0.27,
        },
        PhaseType.BUILD: {
            SportType.SWIMMING: 0.19,
            SportType.CYCLING: 0.54,
            SportType.RUNNING: 0.27,
        },
        PhaseType.PEAK: {
            SportType.SWIMMING: 0.20,
            SportType.CYCLING: 0.51,
            SportType.RUNNING: 0.29,
        },
        PhaseType.TAPER: {
            SportType.SWIMMING: 0.18,
            SportType.CYCLING: 0.52,
            SportType.RUNNING: 0.30,
        },
    },
}


def get_phase_sport_split(
    goal_type: GoalType, phase: PhaseType
) -> dict[SportType, float] | None:
    """Return phase-specific sport split, or ``None`` to use the goal default.

    Args:
        goal_type: The goal type to look up.
        phase: The training phase.

    Returns:
        Phase-keyed sport split dict, or ``None`` if no override exists.
    """
    goal_splits = PHASE_SPORT_SPLITS.get(goal_type)
    if goal_splits is not None:
        return goal_splits.get(phase)
    return None


__all__ = [
    "PHASE_SPORT_SPLITS",
    "get_phase_sport_split",
]
