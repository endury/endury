"""Weekly-layout resolution for the session menus.

The single home of the logic that turns menu data into the specs that
actually fire for one week: flattening a :class:`PhaseMenu` into
``SessionSpec`` tuples for one ambition tier (:func:`flatten_menu`),
phase-fraction filtering plus slot-group resolution
(:func:`active_specs`). The session_generator consumes these at
runtime; the import-time day-layout lints in :mod:`contracts` replay
the same functions so lint truth and runtime truth cannot drift.
"""

from __future__ import annotations

from endury.planner.foundations.types import AmbitionLevel, SportType
from endury.planner.sessions.menus.contracts import sport_of
from endury.planner.sessions.menus.session_spec import SessionSpec
from endury.planner.sessions.menus.types import (
    PhaseMenu,
    Variant,
    resolve_bounds,
)

#: Midpoint distances within this epsilon count as tied during
#: slot-group resolution, so the "later start wins ties" rule engages
#: at exact window boundaries despite float noise (e.g. at phase_frac
#: 3/5 the distances |0.4-0.6| and |0.8-0.6| differ by ~1e-17).
_MIDPOINT_TIE_EPS: float = 1e-9


# Slot-group resolution.


def _place(s: SessionSpec, phase_frac: float, load_frac: float | None) -> float:
    """The week's place a spec's window reads: among the LOAD weeks, else all weeks."""
    return load_frac if s.load_weeks and load_frac is not None else phase_frac


def resolve_slot_groups(
    specs: list[SessionSpec], phase_frac: float, load_frac: float | None = None
) -> list[SessionSpec]:
    """Pick one spec per ``slot_group``, pass ungrouped specs through.

    A ``race_sim`` or ``cadence_weeks`` member wins its group outright
    (the caller only passes race-sim specs through on the chosen
    race-sim week, cadence specs on their cadence weeks). Otherwise the
    winner is the spec whose fraction-range midpoint
    ``(frac_start + frac_end) / 2`` is closest to *phase_frac*;
    distances within ``_MIDPOINT_TIE_EPS`` count as tied, and ties are
    broken in favour of the later ``phase_fraction_start`` (prefer the
    incoming spec at the crossover point).

    Args:
        specs: Active session specs after phase-fraction filtering.
        phase_frac: Current position within the phase run (0..1).
        load_frac: The week's position among the run's LOAD weeks, which
            a ``load_weeks`` spec reads instead; ``None`` outside a LOAD
            week.

    Returns:
        Filtered list with at most one spec per slot group.
    """
    ungrouped: list[SessionSpec] = []
    groups: dict[str, list[SessionSpec]] = {}
    for s in specs:
        if s.slot_group is None:
            ungrouped.append(s)
        else:
            groups.setdefault(s.slot_group, []).append(s)

    winners: list[SessionSpec] = []
    for members in groups.values():
        outright = [s for s in members if s.race_sim or s.cadence_weeks]
        if outright:
            winners.append(outright[0])
            continue

        def midpoint_dist(s: SessionSpec) -> float:
            mid = (s.phase_fraction_start + s.phase_fraction_end) / 2
            return abs(mid - _place(s, phase_frac, load_frac))

        best_dist = min(midpoint_dist(s) for s in members)
        tied = [s for s in members if midpoint_dist(s) <= best_dist + _MIDPOINT_TIE_EPS]
        winners.append(max(tied, key=lambda s: s.phase_fraction_start))

    return ungrouped + winners


def active_specs(
    specs: list[SessionSpec],
    phase_frac: float,
    is_race_sim_week: bool,
    cadence_fire: frozenset[int] = frozenset(),
    load_frac: float | None = None,
) -> list[SessionSpec]:
    """Return the specs that fire for one week, in resolved order.

    Applies the phase-fraction window filter (race-sim and cadence specs
    bypass frac geometry: they fire only on the per-PEAK-run race-sim
    week and on their interval's cadence weeks), then resolves
    mutually-exclusive slot groups. This is exactly the weekly
    instantiation the session_generator performs — order is
    load-bearing for brick-partner wiring (ungrouped specs first, then
    slot-group winners).

    Args:
        specs: Flattened menu specs (plus any maintenance supplements).
        phase_frac: Current position within the phase run (0..1).
        is_race_sim_week: Whether this week hosts the race-sim specs.
        cadence_fire: The ``cadence_weeks`` intervals firing this week.
        load_frac: The week's position among the run's LOAD weeks, which
            a ``load_weeks`` spec reads instead; ``None`` outside a LOAD
            week.

    Returns:
        The specs active this week, at most one per slot group.
    """

    def fires(s: SessionSpec) -> bool:
        if s.race_sim:
            return is_race_sim_week
        if s.cadence_weeks:
            return s.cadence_weeks in cadence_fire
        at = _place(s, phase_frac, load_frac)
        return s.phase_fraction_start <= at <= s.phase_fraction_end

    return resolve_slot_groups([s for s in specs if fires(s)], phase_frac, load_frac)


# Variant -> SessionSpec flattening.


def _emit_variant(
    variant: Variant,
    slot_name: str,
    day_hint: int,
    ambition: AmbitionLevel,
) -> SessionSpec | None:
    """Convert one ``Variant`` to a ``SessionSpec`` for *ambition*.

    Returns ``None`` when *ambition* is absent from the variant's bounds
    (the variant does not fire for this athlete tier).
    """
    bounds = resolve_bounds(variant.bounds, ambition)
    if bounds is None:
        return None
    lo, hi = bounds

    sport = sport_of(variant.activity_key)

    # The runtime's last_duration dict keys on (sport, progression_key).
    # For variants that reference a Progression we use
    # ``progression.name`` so all variants of the same trainable
    # quantity share ramp state, regardless of the activity_key (e.g.
    # RUN_LONG and RUN_LONG_PROGRESSION both referencing IM_LONG_RUN
    # share "im_long_run"). For variants without a Progression we leave
    # it ``None``; the runtime falls back to ``activity_key.value``
    # (single-variant default).
    progression_key = (
        variant.progression.name if variant.progression is not None else None
    )

    common_kwargs: dict = {
        "activity_key": variant.activity_key,
        "sport": sport,
        "progressive": variant.progression is not None,
        "is_brick_pair": variant.is_brick_pair,
        "day_hint": day_hint,
        "phase_fraction_start": variant.frac[0],
        "phase_fraction_end": variant.frac[1],
        "rotation": variant.rotation,
        "slot_group": variant.slot_group,
        "progression_key": progression_key,
        "race_sim": variant.race_sim,
        "key": variant.key,
        "eases": variant.eases.name if variant.eases is not None else None,
        "optional": variant.optional,
        "max_step": variant.max_step,
        "cadence_weeks": variant.cadence_weeks,
        "load_weeks": variant.load_weeks,
        "slot_name": slot_name,
    }
    if sport == SportType.SWIMMING:
        return SessionSpec(
            min_meters=lo,
            max_meters=hi,
            **common_kwargs,
        )
    return SessionSpec(
        min_minutes=lo,
        max_minutes=hi,
        **common_kwargs,
    )


def flatten_menu(menu: PhaseMenu, ambition: AmbitionLevel) -> tuple[SessionSpec, ...]:
    """Flatten a :class:`PhaseMenu` to a tuple of ``SessionSpec``.

    Slot order is preserved so the session_generator's brick-partner
    wiring (most recent cycling spec) keeps producing the expected
    pairings.
    """
    out: list[SessionSpec] = []
    for slot in menu.slots:
        day_hint = int(slot.day)
        for variant in slot.variants:
            spec = _emit_variant(
                variant,
                slot.name,
                day_hint,
                ambition,
            )
            if spec is not None:
                out.append(spec)
    return tuple(out)


__all__ = [
    "active_specs",
    "flatten_menu",
    "resolve_slot_groups",
]
