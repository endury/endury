"""Lookup tables for the session-menu macro planner (macro_planner).

Contains CTL objective weights and the race-day freshness floors used by the L3 QP.
"""

from __future__ import annotations

from endury.planner.foundations.types import GoalType, SportType

# ---------------------------------------------------------------------------
# CTL objective weights — fraction of race fitness owed to each sport
# ---------------------------------------------------------------------------

_RUN_CTL_WEIGHTS: dict[SportType, float] = {
    SportType.RUNNING: 1.0,
    SportType.CYCLING: 0.0,
    SportType.SWIMMING: 0.0,
}

GOAL_CTL_WEIGHTS: dict[GoalType, dict[SportType, float]] = {
    GoalType.RUNNING_5K: _RUN_CTL_WEIGHTS,
    GoalType.RUNNING_10K: _RUN_CTL_WEIGHTS,
    GoalType.RUNNING_HALF_MARATHON: _RUN_CTL_WEIGHTS,
    GoalType.RUNNING_MARATHON: _RUN_CTL_WEIGHTS,
    GoalType.TRAIL_20K: _RUN_CTL_WEIGHTS,
    GoalType.TRAIL_50K: _RUN_CTL_WEIGHTS,
    GoalType.TRAIL_100K: _RUN_CTL_WEIGHTS,
    GoalType.SPRINT_TRIATHLON: {
        SportType.RUNNING: 0.35,
        SportType.CYCLING: 0.40,
        SportType.SWIMMING: 0.25,
    },
    GoalType.OLYMPIC_TRIATHLON: {
        SportType.RUNNING: 0.30,
        SportType.CYCLING: 0.45,
        SportType.SWIMMING: 0.25,
    },
    GoalType.HALF_IRONMAN: {
        SportType.RUNNING: 0.30,
        SportType.CYCLING: 0.55,
        SportType.SWIMMING: 0.15,
    },
    GoalType.IRONMAN: {
        SportType.RUNNING: 0.25,
        SportType.CYCLING: 0.50,
        SportType.SWIMMING: 0.25,
    },
    # Maintenance goals have no race events, so the QP never reads these
    # weights; entries keep the table total over GoalType.
    GoalType.RUNNING_MAINTENANCE: _RUN_CTL_WEIGHTS,
    GoalType.TRAIL_MAINTENANCE: _RUN_CTL_WEIGHTS,
    GoalType.TRIATHLON_MAINTENANCE: {
        SportType.RUNNING: 0.35,
        SportType.CYCLING: 0.40,
        SportType.SWIMMING: 0.25,
    },
}

# ---------------------------------------------------------------------------
# Race-day freshness floor, as TSB / CTL on the combined PMC
#
# The QP holds race-day form at or above RACE_DAY_FRESHNESS_RATIO[priority]
# times race-day fitness, scaled by TAPER_FRESHNESS_SCALE for the taper's
# length. A ratio, not an absolute TSB: an absolute race-ready zone
# (+15..+25 daily) is out of reach for a low-CTL runner and far below a
# high-CTL triathlete, while form over fitness is scale-free, so one floor
# serves every goal and tier (the tier signal is carried by CTL itself).
#
# The A floor is the lower edge of the band measured at the
# race-day instant under per-activity load (docs/research/
# endurance_methodology.md, TSB Zones): 0.27 with a taper of two or more weeks, 0.20
# with a one-week taper. B-races take half the A floor, the ratio of the
# "fresh" zone's midpoint to the "race-ready" zone's (+10 over +20); C-races
# are trained through, with no floor.
# ---------------------------------------------------------------------------

RACE_DAY_FRESHNESS_RATIO: dict[str, float] = {
    "A": 0.27,
    "B": 0.135,
    "C": 0.0,
}

TAPER_FRESHNESS_SCALE: dict[int, float] = {
    1: 0.75,  # only the race week is reduced-load (5K, sprint): 0.20 / 0.27
    2: 1.00,  # two- and three-week tapers reach the same envelope
    3: 1.00,
}

# A goal whose plans arrive like a one-week taper whatever taper
# Endury gives it, read at that taper length: 10K plans arrive at 0.20-0.25
# (docs/research/endurance_methodology.md, TSB Zones),
# under the 0.27 of the longer tapers.
GOAL_FRESHNESS_TAPER_WEEKS: dict[GoalType, int] = {GoalType.RUNNING_10K: 1}


__all__ = [
    "GOAL_CTL_WEIGHTS",
    "GOAL_FRESHNESS_TAPER_WEEKS",
    "RACE_DAY_FRESHNESS_RATIO",
    "TAPER_FRESHNESS_SCALE",
]
