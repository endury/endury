"""Bounds helpers for the menu loader.

:func:`scaled` is the YAML loader's ``{scaled: [lo, hi]}`` shorthand;
:func:`swim_floor_metres` is the least a swim may hold, which the menu
contracts and the session generator both enforce.
"""

from __future__ import annotations

from endury.planner.foundations.types import ActivityKey, AmbitionLevel, SportType
from endury.planner.sessions.menus.types import Bounds


def scaled(lo: int, hi: int) -> Bounds:
    """Per-tier bounds derived from COMPETITIVE literals.

    The loader's ``{scaled: [lo, hi]}`` shorthand: COMPETITIVE
    magnitudes, with BASIC/MODERATE derived at the canonical 0.65/0.85
    scales (rounded to integer minutes/metres).

    Args:
        lo: COMPETITIVE lower bound.
        hi: COMPETITIVE upper bound.
    """
    return {
        AmbitionLevel.BASIC: (round(lo * 0.65), round(hi * 0.65)),
        AmbitionLevel.MODERATE: (round(lo * 0.85), round(hi * 0.85)),
        AmbitionLevel.COMPETITIVE: (lo, hi),
    }


#: The least a load or taper swim holds, per tier. A swim much shorter than
#: this spends its time on warm-up and cool-down and leaves too little main
#: set to move the athlete forward.
SWIM_FLOOR_METRES: dict[AmbitionLevel, int] = {
    AmbitionLevel.BASIC: 1500,
    AmbitionLevel.MODERATE: 1800,
    AmbitionLevel.COMPETITIVE: 2000,
}

#: Swims sized by their own purpose, not by the floor: technique drills and
#: the fixed CSS test protocol.
_SWIM_FLOOR_EXEMPT: frozenset[ActivityKey] = frozenset(
    {ActivityKey.SWIM_DRILL, ActivityKey.SWIM_CSS_TEST}
)


def swim_floor_metres(
    sport: SportType,
    activity_key: ActivityKey,
    mode_key: str,
    ambition: AmbitionLevel,
) -> int:
    """Return the least metres a session may hold, or 0 when no floor applies.

    Only load and taper swims carry the floor: recovery and race weeks are
    lighter on purpose, and drills and tests are exempt.

    Args:
        sport: The session's sport.
        activity_key: The activity booked.
        mode_key: The menu mode (``load``, ``recovery``, ``taper``, ``race``).
        ambition: The athlete's tier.
    """
    if (
        sport != SportType.SWIMMING
        or mode_key not in ("load", "taper")
        or activity_key in _SWIM_FLOOR_EXEMPT
    ):
        return 0
    return SWIM_FLOOR_METRES[ambition]


__all__ = [
    "SWIM_FLOOR_METRES",
    "scaled",
    "swim_floor_metres",
]
