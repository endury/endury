"""Tunable scalar parameters for the L1 phase planner.

These are policy knobs — change them to adjust planning behaviour without
touching algorithm code.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Sport-split blending weights
# ---------------------------------------------------------------------------

BLEND_WEIGHT_A: float = 1.0
BLEND_WEIGHT_B: float = 0.8
BLEND_WEIGHT_C: float = 0.3

# ---------------------------------------------------------------------------
# Recovery cadence
# ---------------------------------------------------------------------------

# No recovery within N weeks of the A-goal taper's start (R-PHASE-015). A
# taper of LONG_TAPER_WEEKS or more (the Ironman's three) keeps the wider
# window and exempts PEAK, whose load demands a deload the taper then adds
# to; a shorter taper takes the narrow window with no exemption, so the last
# load block is two or three weeks and no recovery week sits beside a
# one-week taper.
TAPER_SUPPRESSION_WEEKS: int = 3
SHORT_TAPER_SUPPRESSION_WEEKS: int = 2
LONG_TAPER_WEEKS: int = 3

# A B race whose GoalProfile.race_duration sums past this many hours (a
# marathon, a trail 20K and up, a half or full Ironman) is followed by one
# RACE_RECOVERY week (R-PHASE-010); shorter tune-ups train straight through.
B_RACE_RECOVERY_MIN_HOURS: float = 3.0

# ---------------------------------------------------------------------------
# Validation thresholds
# ---------------------------------------------------------------------------

MAX_HORIZON_WEEKS: int = 52
BC_PROXIMITY_TO_A_RACE_WEEKS: int = 2  # no B/C within this of A-race
BC_EARLIEST_WEEK: int = 3  # no B/C in first N weeks
BC_WINDOW_FRACTION: float = 0.40  # B/C race weeks <= this of pre-taper weeks


__all__ = [
    "BC_EARLIEST_WEEK",
    "BC_PROXIMITY_TO_A_RACE_WEEKS",
    "BC_WINDOW_FRACTION",
    "BLEND_WEIGHT_A",
    "BLEND_WEIGHT_B",
    "BLEND_WEIGHT_C",
    "B_RACE_RECOVERY_MIN_HOURS",
    "LONG_TAPER_WEEKS",
    "MAX_HORIZON_WEEKS",
    "SHORT_TAPER_SUPPRESSION_WEEKS",
    "TAPER_SUPPRESSION_WEEKS",
]
