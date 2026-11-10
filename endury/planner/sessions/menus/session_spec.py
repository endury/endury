"""Runtime ``SessionSpec`` — the L3 generator's session record.

This is the post-adapter currency: the blueprint Variant model is
public-facing (data + contracts), and ``SessionSpec`` is what the
session_generator consumes per week. Kept in its own module so the
blueprint/types and the runtime stay decoupled.

``slot_group``, ``rotation`` and ``progression_key`` are the
generator's live selection and ramp-state mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass

from endury.planner.foundations.types import ActivityKey, SportType


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """One prescribed session in a weekly training menu.

    Non-swim sessions encode duration bounds in **minutes** via
    ``min_minutes`` / ``max_minutes``; swim sessions encode **distance
    bounds in metres** via ``min_meters`` / ``max_meters`` (converted
    to minutes at plan time using the athlete's CSS).

    Args:
        activity_key: Which activity to perform.
        sport: Sport of the session.
        min_minutes: Duration lower bound at phase start (non-swim).
        max_minutes: Duration upper bound at phase end (non-swim).
        progressive: If True, grow from athlete's current longest session.
        is_brick_pair: If True, co-locate with the preceding session.
        day_hint: Authored day of week (0=Mon..6=Sun). -1 = no authored
            day (defensive: the day assigner falls back to Monday) —
            menu-origin specs always carry a real day (Slot.day is
            mandatory).
        phase_fraction_start: Earliest phase fraction this session appears.
        phase_fraction_end: Latest phase fraction (1.0 = full phase).
        rotation: Activity-key cycle for week-over-week variant rotation.
        slot_group: Mutually-exclusive group name. The generator picks
            one per ``slot_group`` per week based on phase-fraction
            proximity.
        progression_key: Shared state key for progressive duration growth.
            ``None`` falls back to ``activity_key.value`` (the default
            single-variant case).
        race_sim: When True, the spec ignores its fraction window and
            fires only on the generator's per-PEAK-run race-sim week,
            winning its ``slot_group`` outright on that week.
        min_meters: Distance lower bound at phase start (swim only).
        max_meters: Distance upper bound at phase end (swim only).
        slot_name: Authoring ``Slot.name`` this spec was flattened from
            (unique within its menu). Provenance only — the dossier
            renders it so a reviewer can jump from an emitted session
            straight to the menu slot. ``None`` for specs not built
            from a blueprint Slot (maintenance menus).
        key: The key-session share of a squeezed week (``Variant.key``);
            ``0.0`` = not key.
        eases: Name of the progression whose longest this session stays
            under (``Variant.eases``); ``None`` = sized by its bounds alone.
        optional: An extra the plan does not count on (``Variant.optional``).
        max_step: A rehearsal's own step (``Variant.max_step``); ``None`` =
            the default step.
        cadence_weeks: Set, the spec ignores its fraction window and fires
            only on the generator's cadence weeks for that interval
            (``Variant.cadence_weeks``), winning its ``slot_group``
            outright on those weeks; ``None`` = not a cadence session.
        load_weeks: In a LOAD week, the spec reads its fraction window at
            the week's place among its phase run's LOAD weeks rather than
            among all its weeks (``Variant.load_weeks``).
    """

    activity_key: ActivityKey
    sport: SportType
    min_minutes: int = 0
    max_minutes: int = 0
    progressive: bool = False
    is_brick_pair: bool = False
    day_hint: int = -1
    phase_fraction_start: float = 0.0
    phase_fraction_end: float = 1.0
    rotation: tuple[ActivityKey, ...] = ()
    slot_group: str | None = None
    progression_key: str | None = None
    race_sim: bool = False
    min_meters: int = 0
    max_meters: int = 0
    slot_name: str | None = None
    key: float = 0.0
    eases: str | None = None
    optional: bool = False
    max_step: float | None = None
    cadence_weeks: int | None = None
    load_weeks: bool = False

    def __post_init__(self) -> None:
        """Validate that swim entries use metres and non-swim entries use minutes."""
        if self.sport == SportType.SWIMMING:
            if self.min_minutes != 0 or self.max_minutes != 0:
                msg = (
                    f"Swim spec {self.activity_key.value} must use "
                    f"min_meters/max_meters, not min_minutes/max_minutes"
                )
                raise ValueError(msg)
            if self.min_meters <= 0 or self.max_meters <= 0:
                msg = (
                    f"Swim spec {self.activity_key.value} must set "
                    f"positive min_meters/max_meters"
                )
                raise ValueError(msg)
        else:
            if self.min_meters != 0 or self.max_meters != 0:
                msg = (
                    f"Non-swim spec {self.activity_key.value} must not "
                    f"set min_meters/max_meters"
                )
                raise ValueError(msg)
            if self.min_minutes <= 0 or self.max_minutes <= 0:
                msg = (
                    f"Non-swim spec {self.activity_key.value} must set "
                    f"positive min_minutes/max_minutes"
                )
                raise ValueError(msg)


__all__ = ["SessionSpec"]
