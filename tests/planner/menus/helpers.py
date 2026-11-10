"""Test-only builders for hand-made menu fixtures.

Menus are authored in YAML; these build the same dataclasses directly for
contract and generator tests that need a small, specific blueprint.
"""

from __future__ import annotations

from endury.planner.foundations.types import ActivityKey
from endury.planner.sessions.menus.types import (
    Bounds,
    Progression,
    Rationale,
    Slot,
    Variant,
    Weekday,
)


def _to_rationale(value: Rationale | str | None) -> Rationale | None:
    """Lift a bare string into a Rationale; pass through other inputs."""
    if value is None or isinstance(value, Rationale):
        return value
    return Rationale(intent=value)


def session(
    name: str,
    day: Weekday,
    activity: ActivityKey,
    bounds: Bounds,
    *,
    progression: Progression | None = None,
    is_brick_pair: bool = False,
    rationale: Rationale | str | None = None,
    slot_group: str | None = None,
    rotation: tuple[ActivityKey, ...] = (),
    frac: tuple[float, float] = (0.0, 1.0),
    race_sim: bool = False,
    cadence_weeks: int | None = None,
) -> Slot:
    """Single-Variant Slot — the common authoring case.

    Args:
        name: Slot name.
        day: Authored weekday for the slot.
        activity: Activity key for the only Variant.
        bounds: Duration bounds for the only Variant.
        progression: Optional Progression reference.
        is_brick_pair: When True, marks the Variant as a brick partner.
        rationale: Optional coaching note (bare string is wrapped).
        slot_group: Slot-group label passed through to the Variant.
        rotation: Activity-key rotation tuple passed through to the Variant.
        frac: Phase-fraction window applied to the Variant. Useful for
            single-Variant slots that only fire in a sub-phase window.
        race_sim: When True, the Variant fires only on the generator's
            per-PEAK-run race-sim week (bypassing ``frac``).
        cadence_weeks: Set, the Variant fires only on the generator's
            cadence weeks for that interval (bypassing ``frac``).
    """
    variant = Variant(
        activity_key=activity,
        bounds=bounds,
        frac=frac,
        progression=progression,
        is_brick_pair=is_brick_pair,
        rationale=_to_rationale(rationale),
        slot_group=slot_group,
        rotation=rotation,
        race_sim=race_sim,
        cadence_weeks=cadence_weeks,
    )
    return Slot(
        name=name,
        day=day,
        variants=(variant,),
    )
