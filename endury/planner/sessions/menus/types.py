"""Type definitions for the session-menu data layer.

These types describe a goal's training menus as composable, queryable
objects rather than flat ``SessionSpec`` tuples. The contract is in
``docs/specs/planner/session_generator.md``.

Frozen dataclasses throughout; ``__post_init__`` enforces simple value
invariants. Cross-Variant / cross-Slot invariants belong to
``contracts.py``.

Selection model: a ``Slot`` is one weekday carrying one prescription;
``slot_group`` is the mutual-exclusion picker that chooses between
competing Variants for the week; ``frac`` is the eligibility window
feeding that choice; ``rotation`` swaps the emitted activity key by
week offset. Every field below either drives runtime/validator
behaviour or is ``Rationale`` — write-only authoring metadata kept for
a future catalog/dossier renderer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import IntEnum
from types import MappingProxyType

from endury.planner.foundations.types import (
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    SportType,
)

#: Duration bounds. Either a shared ``(lo, hi)`` tuple applied across all
#: ambition tiers, or a per-tier dict. When the dict form is used, tiers
#: absent from the dict mean this variant does not fire for those athletes.
#: Units: minutes for non-swim activities, metres for swim.
Bounds = tuple[int, int] | dict[AmbitionLevel, tuple[int, int]]


def resolve_bounds(bounds: Bounds, ambition: AmbitionLevel) -> tuple[int, int] | None:
    """Return ``(lo, hi)`` for *ambition* or ``None`` if not applicable."""
    if isinstance(bounds, tuple):
        return bounds
    return bounds.get(ambition)


def bounds_ambitions(bounds: Bounds) -> frozenset[AmbitionLevel]:
    """Return ambition tiers for which *bounds* fires."""
    if isinstance(bounds, tuple):
        return frozenset(AmbitionLevel)
    return frozenset(bounds.keys())


class Weekday(IntEnum):
    """Ordered weekday index matching Python's ``date.weekday()``."""

    MON = 0
    TUE = 1
    WED = 2
    THU = 3
    FRI = 4
    SAT = 5
    SUN = 6


@dataclass(frozen=True, slots=True)
class Rationale:
    """Optional coaching metadata attached to a Variant, Slot, or Progression.

    Args:
        intent: One-line plain-English description (dev-facing).
        athlete_why: Athlete-facing "why this session" prose, rendered
            verbatim on the session card. Self-contained coaching
            language only — no citations, section refs, author names, or
            dev-speak (those belong in ``intent``/``citations``).
        citations: References supporting the prescription.
        last_reviewed: Date of last manual review. Acts as the staleness
            signal; change history lives in git.
    """

    intent: str = ""
    athlete_why: str = ""
    citations: tuple[str, ...] = ()
    last_reviewed: date | None = None


@dataclass(frozen=True, slots=True)
class Progression:
    """Shared ramp identity for one trainable quantity.

    Variants referencing the same Progression share week-over-week ramp
    state in the generator (keyed on ``(sport, name)``), so a trajectory
    carries continuously across activity-key swaps (e.g. ``RUN_LONG`` →
    ``RUN_LONG_PROGRESSION``). The ramp itself is computed by the
    generator within each Variant's ``bounds`` — a Progression carries
    no magnitudes of its own.

    Args:
        name: Stable ramp-state key (also used in catalog navigation).
        rationale: Optional coaching metadata.
    """

    name: str
    rationale: Rationale | None = None


@dataclass(frozen=True, slots=True)
class Variant:
    """One concrete prescription within a Slot.

    Args:
        activity_key: Which activity to perform.
        bounds: Duration band(s). See :data:`Bounds`.
        frac: ``(start, end)`` phase-fraction window. Defaults to the
            whole phase. Multi-Variant slots disambiguate by ``frac``.
        progression: Reference to a Progression object. ``None`` means
            non-progressive.
        is_brick_pair: When True, the generator wires ``brick_partner``
            to the most recent preceding cycling spec — which the
            ``brick_partner_day`` lint keeps on the same authored day.
        rationale: Optional coaching metadata.
        slot_group: Mutual-exclusion group name, passed through to
            ``SessionSpec.slot_group``. Specs sharing a group compete
            and exactly one fires per week.
        rotation: Activity-key cycle for week-over-week rotation,
            passed through to ``SessionSpec.rotation``.
        race_sim: When True, the variant bypasses ``frac`` geometry and
            fires only on the per-PEAK-run race-sim week chosen by the
            generator (exactly one LOAD week ≈5 weeks before the race),
            winning its ``slot_group`` outright on that week.
        key: Non-zero marks a key session and is the largest share of a
            squeezed week it may take: when the week is short on time, a
            key session keeps its size and the other sessions give way
            first (session_generator R-SESS-033). ``0.0`` = not key.
        eases: The Progression this easier-week session stands in for:
            it is never longer than 0.75 of the longest that progression
            has reached (session_generator R-SESS-034). ``None`` = sized by
            its bounds alone.
        optional: An extra the plan does not count on: booked at its
            target, outside the week's capacity and the load model
            (session_generator R-SESS-040).
        max_step: A rehearsal's own step: it fires only while its minimum is
            at most ``1 + max_step`` times the least its progression was last
            booked or the athlete's recent longest (session_generator
            R-SESS-023). ``None`` = the default step.
        cadence_weeks: Set, the variant bypasses ``frac`` geometry and
            fires only on the plan's cadence weeks for that interval: the
            first week, then the recovery week nearest every
            ``cadence_weeks`` plan weeks, never in PEAK or TAPER
            (session_generator ``cadence_weeks``, R-SESS-041), winning
            its ``slot_group`` outright on those weeks. ``None`` = not a
            cadence session.
        load_weeks: In a LOAD week, the variant reads ``frac`` at the
            week's place among its phase run's LOAD weeks, so a recovery
            week does not move it (session_generator R-SESS-008).
    """

    activity_key: ActivityKey
    bounds: Bounds
    frac: tuple[float, float] = (0.0, 1.0)
    progression: Progression | None = None
    is_brick_pair: bool = False
    rationale: Rationale | None = None
    slot_group: str | None = None
    rotation: tuple[ActivityKey, ...] = ()
    race_sim: bool = False
    key: float = 0.0
    eases: Progression | None = None
    optional: bool = False
    max_step: float | None = None
    cadence_weeks: int | None = None
    load_weeks: bool = False

    def __post_init__(self) -> None:
        """Validate frac window, bounds and the optional, step and cadence fields."""
        lo, hi = self.frac
        if not (0.0 <= lo <= hi <= 1.0):
            msg = (
                f"Variant {self.activity_key.value} has invalid frac window {self.frac}"
            )
            raise ValueError(msg)
        if self.optional and (self.key or self.progression or self.race_sim):
            msg = (
                f"Variant {self.activity_key.value} is optional, so it cannot be "
                f"a key, ramp or race-sim session"
            )
            raise ValueError(msg)
        if self.cadence_weeks is not None and (self.cadence_weeks < 2 or self.race_sim):
            msg = (
                f"Variant {self.activity_key.value} needs a cadence_weeks of at "
                f"least 2 and cannot also be a race-sim session"
            )
            raise ValueError(msg)
        if self.max_step is not None and not (self.race_sim and self.progression):
            msg = (
                f"Variant {self.activity_key.value} has a max_step but is not a "
                f"race-sim ramp session"
            )
            raise ValueError(msg)
        if isinstance(self.bounds, tuple):
            blo, bhi = self.bounds
            if blo > bhi:
                msg = (
                    f"Variant {self.activity_key.value} has bounds "
                    f"{self.bounds} with lo > hi"
                )
                raise ValueError(msg)
        else:
            if not self.bounds:
                msg = (
                    f"Variant {self.activity_key.value} has empty per-tier bounds dict"
                )
                raise ValueError(msg)
            for tier, (blo, bhi) in self.bounds.items():
                if blo > bhi:
                    msg = (
                        f"Variant {self.activity_key.value} bounds for "
                        f"{tier} has lo > hi"
                    )
                    raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Slot:
    """A position on the weekly grid.

    See :mod:`endury.planner.sessions.menus.types` module docstring for the
    selection semantics.

    Args:
        name: Stable identifier.
        day: Authored weekday — every slot owns a day on the weekly grid.
        variants: One or more Variants.
        rationale: Optional slot-level rationale.
    """

    name: str
    day: Weekday
    variants: tuple[Variant, ...]
    rationale: Rationale | None = None

    def __post_init__(self) -> None:
        """Validate the slot has at least one variant."""
        if not self.variants:
            msg = f"Slot {self.name!r} has no variants"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class MenuContract:
    """Invariants every PhaseMenu must satisfy (per ambition tier).

    All fields are optional — empty collections / ``None`` mean
    "no constraint". Every field is enforced at import time by
    ``contracts.validate_blueprint``: ``must_contain``,
    ``must_not_contain``, ``min_sport_sessions``, ``bricks_per_week``,
    and ``weekly_quality_sessions`` (Z4+ threshold/interval families).
    """

    must_contain: frozenset[ActivityKey] = frozenset()
    must_not_contain: frozenset[ActivityKey] = frozenset()
    min_sport_sessions: Mapping[SportType, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
    weekly_quality_sessions: tuple[int, int] | None = None
    bricks_per_week: tuple[int, int] | None = None


@dataclass(frozen=True, slots=True)
class PhaseMenu:
    """All slots for one (phase, mode_key) of a goal blueprint.

    Args:
        phase: Owning training phase.
        mode_key: ``"load"`` / ``"recovery"`` / ``"taper"`` / ``"race"``.
        slots: Ordered tuple. Order matters for brick wiring (a brick
            Variant pairs with the preceding cycling slot).
        contract: Invariants validated at import time.
    """

    phase: PhaseType
    mode_key: str
    slots: tuple[Slot, ...]
    contract: MenuContract = MenuContract()


@dataclass(frozen=True, slots=True)
class PlanBlueprint:
    """Complete menu set for one goal type.

    Args:
        goal: Race goal type.
        phases: ``(phase, mode_key) -> PhaseMenu`` lookup. Validated
            against the expected coverage matrix at import time.
        offered: The ambition tiers a new plan may be built at. Empty: the
            goal is not offered. The planner builds every tier regardless;
            ``endury.planning`` refuses a new plan outside this set.
    """

    goal: GoalType
    phases: Mapping[tuple[PhaseType, str], PhaseMenu]
    offered: frozenset[AmbitionLevel] = frozenset()


__all__ = [
    "Bounds",
    "MenuContract",
    "PhaseMenu",
    "PlanBlueprint",
    "Progression",
    "Rationale",
    "Slot",
    "Variant",
    "Weekday",
    "bounds_ambitions",
    "resolve_bounds",
]
