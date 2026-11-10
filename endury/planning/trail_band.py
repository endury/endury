"""The trail km-effort table: race profile -> suggested trail band.

Mountain-trail goals are categorized by effort, not raw distance — a
short race with heavy climb behaves like a much longer one. km-effort
(``distance_km + climb_m / 100``: 100 m of climb reads like one extra km)
is the standard rule of thumb; the three bands below are the single Python
home for it. Endury plans no trail race of 170 km-effort or more (the
100-mile band: too little evidence for its menus). ``web/shells/shared.js``
mirrors this table; keep both in sync.
"""

from __future__ import annotations

from endury.planner.foundations.types import GoalType
from endury.planning.coach_brief import _goal_label

#: Ascending km-effort upper bounds (exclusive), each paired with the band
#: below it. Effort at or beyond the last bound has no band.
BAND_THRESHOLDS: tuple[tuple[float, GoalType], ...] = (
    (50, GoalType.TRAIL_20K),
    (100, GoalType.TRAIL_50K),
    (170, GoalType.TRAIL_100K),
)

#: The km-effort from which Endury has no trail plan.
LONGEST_PLANNED_EFFORT: float = BAND_THRESHOLDS[-1][0]

#: Every race-band trail goal the table can suggest (excludes the raceless
#: trail_maintenance goal, which has no km-effort of its own).
TRAIL_RACE_GOALS: frozenset[str] = frozenset(goal.value for _, goal in BAND_THRESHOLDS)


def km_effort(distance_km: float, climb_m: float) -> float:
    """UTMB-style effort: 100 m of climb reads like one extra km.

    Args:
        distance_km: Race distance in kilometers.
        climb_m: Race climb in meters.

    Returns:
        The km-effort score.
    """
    return distance_km + climb_m / 100


def trail_goal_for(distance_km: float, climb_m: float) -> GoalType | None:
    """The trail band a race profile falls into, by km-effort.

    Args:
        distance_km: Race distance in kilometers.
        climb_m: Race climb in meters.

    Returns:
        ``TRAIL_20K`` under 50 km-effort, ``TRAIL_50K`` 50-99,
        ``TRAIL_100K`` 100-169, ``None`` from 170: no Endury plan.
    """
    effort = km_effort(distance_km, climb_m)
    for bound, goal in BAND_THRESHOLDS:
        if effort < bound:
            return goal
    return None


def band_thresholds_prose() -> str:
    """Render the band table as '<50 Trail 20K, 50-99 Trail 50K, …' prose.

    Quoted verbatim by ``context.build_app_knowledge``, so the
    athlete-visible thresholds can never drift from the table above.

    Returns:
        The comma-joined threshold prose.
    """
    parts: list[str] = []
    lower: float = 0
    for bound, goal in BAND_THRESHOLDS:
        label = _goal_label(goal.value)
        if lower == 0:
            parts.append(f"<{bound} {label}")
        else:
            parts.append(f"{lower}-{bound - 1} {label}")
        lower = bound
    parts.append(f"{lower}+ no plan")
    return ", ".join(parts)


__all__ = [
    "BAND_THRESHOLDS",
    "LONGEST_PLANNED_EFFORT",
    "TRAIL_RACE_GOALS",
    "band_thresholds_prose",
    "km_effort",
    "trail_goal_for",
]
