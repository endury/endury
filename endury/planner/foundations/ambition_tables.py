"""Ambition-level lookup table — the single source of per-tier policy knobs.

Each ``AmbitionLevel`` maps to an ``AmbitionProfile`` consumed by the phase
planner (recovery cadence), the capacity projector, and the macro QP (ramp
limit, recovery ceiling). ``macro_config.py`` keeps the MODERATE values as its
own reference defaults only.

The planner's primary currency is training hours; ``max_weekly_hours_ceiling``
is the capacity limiter. Race-day freshness is not a per-tier knob: the QP
holds form over fitness at a floor shared by every tier
(``macro_tables.RACE_DAY_FRESHNESS_RATIO``), since the tier signal is already
carried by CTL.
"""

from __future__ import annotations

from endury.planner.foundations.types import AmbitionLevel, AmbitionProfile

# Per-level profiles

AMBITION_PROFILES: dict[AmbitionLevel, AmbitionProfile] = {
    AmbitionLevel.BASIC: AmbitionProfile(
        hours_ramp_limit=1.07,  # 7 %/wk — conservative growth
        # 3:1 like the other tiers (was every 3rd LOAD week, 2:1): at 2:1 a
        # basic trail 20K averaged the athlete's recent volume over 12 weeks
        # and its long run topped out at 43 % of the race; the trail research
        # deloads every 3rd or 4th week.
        recovery_cadence=4,  # recovery every 4th LOAD → 3:1
        max_weekly_hours_ceiling=13.0,  # compound-ramp blowout guard
        # 0.60 like MODERATE (was 0.50): recovery weeks read 0.47 of the load
        # week on the trail 20K, under the 0.5-0.7 band and the planner's
        # own "about half".
        recovery_load_fraction=0.60,
    ),
    AmbitionLevel.MODERATE: AmbitionProfile(
        hours_ramp_limit=1.10,  # 10 %/wk — standard growth
        recovery_cadence=4,  # recovery every 4th LOAD → 3:1
        max_weekly_hours_ceiling=16.0,
        recovery_load_fraction=0.60,
    ),
    AmbitionLevel.COMPETITIVE: AmbitionProfile(
        hours_ramp_limit=1.12,  # 12 %/wk — aggressive growth
        recovery_cadence=4,  # recovery every 4th LOAD → 3:1
        max_weekly_hours_ceiling=22.0,  # elite-tier ceiling
        recovery_load_fraction=0.65,  # lighter deloads
    ),
}


def get_ambition_profile(level: AmbitionLevel) -> AmbitionProfile:
    """Look up the ``AmbitionProfile`` for a given level.

    Args:
        level: The ambition level.

    Returns:
        Frozen ``AmbitionProfile`` with all per-level knobs.
    """
    return AMBITION_PROFILES[level]


__all__ = [
    "AMBITION_PROFILES",
    "get_ambition_profile",
]
