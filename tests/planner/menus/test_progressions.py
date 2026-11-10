"""Tests for the Ironman blueprint's progressions."""

from __future__ import annotations

from endury.planner.foundations.types import ActivityKey, GoalType
from endury.planner.sessions.menus import BLUEPRINTS

_EXPECTED_NAMES = frozenset(
    {
        "im_long_run",
        "im_long_bike",
        "im_long_swim",
        "im_midweek_bike",
        "im_build_brick_run",
    }
)


def _variants_by_progression() -> dict[str, set[ActivityKey]]:
    """Every progression name -> the activity keys of variants that use it."""
    by_name: dict[str, set[ActivityKey]] = {}
    for menu in BLUEPRINTS[GoalType.IRONMAN].phases.values():
        for slot in menu.slots:
            for variant in slot.variants:
                if variant.progression is not None:
                    by_name.setdefault(variant.progression.name, set()).add(
                        variant.activity_key
                    )
    return by_name


def test_progression_names_are_stable():
    """The name is the live payload: variants share ramp state through it."""
    assert set(_variants_by_progression()) == _EXPECTED_NAMES


def test_im_long_run_shared_across_activity_key_swap():
    """RUN_LONG and RUN_LONG_PROGRESSION variants share one ramp identity.

    The runtime keys ramp state on ``(sport, progression.name)``, so the
    trajectory must carry continuously across the BASE/BUILD -> PEAK
    activity-key swap.
    """
    activities = _variants_by_progression()["im_long_run"]
    assert ActivityKey.RUN_LONG in activities
    assert ActivityKey.RUN_LONG_PROGRESSION in activities


def test_im_long_bike_shared_across_activity_key_swap():
    """BASE's BIKE_LONG and BUILD's BIKE_LONG_SWEET_SPOT share one ramp."""
    activities = _variants_by_progression()["im_long_bike"]
    assert ActivityKey.BIKE_LONG in activities
    assert ActivityKey.BIKE_LONG_SWEET_SPOT in activities
