"""Centralised configuration constants and resource tables for the activity library.

Zone threshold tables, hardness descriptors, workout-step constants, and swim
pool constants all live here so that sport-specific modules and the base class
share a single source of truth.

All zone tables are **derived** from the canonical band specs in
``endury.training.zones``.  Every band spec is in IF-space
(ascending — higher value = harder effort), so the zone upper bound is
simply the spec's ``high_factor`` for all four metrics.
"""

from __future__ import annotations

from endury.planner.foundations.types import ActivityHardness
from endury.training.load import (
    RUNNING_THRESHOLD_TSS_PER_HOUR as RUNNING_THRESHOLD_TSS_PER_HOUR,
)
from endury.training.zones import (
    HR_BAND_SPECS,
    PACE_BAND_SPECS,
    POWER_BAND_SPECS,
    SWIM_PACE_BAND_SPECS,
)

# Workout-step constants

#: TSS threshold below which a session is treated as rest (no training load).
REST_TSS_THRESHOLD: float = 0.25

#: +/- 3 % band around the computed target value for pace/power prescriptions.
TARGET_BAND_FRACTION: float = 0.03

# Zone threshold tables (zone_number, IF upper bound), used by
# ``_if_to_zone`` to map an intensity factor to a zone number. All derived
# from the domain's canonical ZoneBandSpec tables.


def _upper_bounds(
    specs: tuple,  # tuple[ZoneBandSpec, ...]
) -> tuple[tuple[int, float], ...]:
    """Extract (zone_number, high_factor) pairs from band specs."""
    return tuple((s.zone_number, s.high_factor) for s in specs)


HR_ZONE_THRESHOLDS: tuple[tuple[int, float], ...] = _upper_bounds(HR_BAND_SPECS)
POWER_ZONE_THRESHOLDS: tuple[tuple[int, float], ...] = _upper_bounds(POWER_BAND_SPECS)
RUNNING_PACE_ZONE_THRESHOLDS: tuple[tuple[int, float], ...] = _upper_bounds(
    PACE_BAND_SPECS
)
SWIM_PACE_ZONE_THRESHOLDS: tuple[tuple[int, float], ...] = _upper_bounds(
    SWIM_PACE_BAND_SPECS
)

# Zone floors (lowest IF the band table covers). The HR and power tables
# start at 0.00, so every intensity factor lands in some band. The pace
# tables are the inverse of the HR table and start at ``1 / 1.40`` (~0.714)
# so the inverted builder can emit a finite slow-pace boundary — an
# easier-than-Z1 effort (a warm-up at IF 0.65) falls off the bottom of the
# table and has no honest zone number.


def _lowest_bound(
    specs: tuple,  # tuple[ZoneBandSpec, ...]
) -> float:
    """Return the lowest intensity factor covered by the band specs."""
    return float(specs[0].low_factor)


POWER_ZONE_FLOOR: float = _lowest_bound(POWER_BAND_SPECS)
RUNNING_PACE_ZONE_FLOOR: float = _lowest_bound(PACE_BAND_SPECS)

#: Heart-rate band (fractions of LTHR) for the easy main step of a long
#: session. HR Zone 1 spans 0-85 % LTHR, so without it the main set would
#: target the same band as its own warm-up and cool-down; an easy long
#: effort sits in the top of that zone.
LONG_EASY_HR_BAND: tuple[float, float] = (0.75, 0.85)

# Swim pool constants

#: Swim distance granularity: every swim rep/step distance the planner emits
#: is a whole number of 50 m lengths, so every card swims cleanly in a 25 m
#: or a 50 m pool (a 75 m rep is impossible in a 50 m pool).
SWIM_STEP_METERS: int = 50

#: Conversion factor: 100 metres x 60 seconds = 6000.
PACE_DISTANCE_TO_MINUTES: float = 6000.0

#: Critical swim speed (s/100 m) assumed when the athlete has none on file:
#: 120 s/100 m, i.e. 50 m/min at threshold. The one fallback for every swim
#: path — the session generator budgets menu metres at it and the swim shapes
#: render cards at it — so an athlete without a CSS swims the metres the
#: plan sized.
DEFAULT_SWIM_CSS_SEC_PER_100M: float = 120.0

# Hardness descriptors

EASY_HARDNESS = ActivityHardness(
    is_hard_overall=False,
    is_hard_physiologically=False,
    is_hard_mentally=False,
)
HARD_HARDNESS = ActivityHardness(
    is_hard_overall=True,
    is_hard_physiologically=True,
    is_hard_mentally=True,
)
MODERATE_HARDNESS = ActivityHardness(
    is_hard_overall=True,
    is_hard_physiologically=False,
    is_hard_mentally=False,
)

__all__ = [
    "DEFAULT_SWIM_CSS_SEC_PER_100M",
    "EASY_HARDNESS",
    "HARD_HARDNESS",
    "HR_ZONE_THRESHOLDS",
    "LONG_EASY_HR_BAND",
    "MODERATE_HARDNESS",
    "PACE_DISTANCE_TO_MINUTES",
    "POWER_ZONE_FLOOR",
    "POWER_ZONE_THRESHOLDS",
    "REST_TSS_THRESHOLD",
    "RUNNING_PACE_ZONE_FLOOR",
    "RUNNING_PACE_ZONE_THRESHOLDS",
    "SWIM_PACE_ZONE_THRESHOLDS",
    "SWIM_STEP_METERS",
    "TARGET_BAND_FRACTION",
]
