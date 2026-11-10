"""Import-time contract validation for menu blueprints.

Every ``<goal>.py`` calls :func:`validate_blueprint` at the bottom of the
module. ``menus/__init__.py`` additionally calls :func:`validate_all` for
cross-blueprint invariants. Failures raise :class:`MenuContractError`
immediately so misconfigurations surface in every test run, every CLI
invocation, every web request.

See ``docs/specs/planner/session_generator.md``
for the menu data-model contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise, product
from typing import TYPE_CHECKING

from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    SportType,
    is_maintenance_goal,
)
from endury.planner.sessions.menus.patterns import swim_floor_metres
from endury.planner.sessions.menus.types import (
    MenuContract,
    PhaseMenu,
    PlanBlueprint,
    Slot,
    Variant,
    Weekday,
    bounds_ambitions,
    resolve_bounds,
)

if TYPE_CHECKING:
    from endury.planner.sessions.menus.session_spec import SessionSpec

# Activity-key -> SportType lookup. Kept here rather than on ``ActivityKey``
# (a plain string enum) so the menus module owns its own sport
# classification table.

_SPORT_BY_KEY: Mapping[ActivityKey, SportType] = {
    # Running
    ActivityKey.RUN_EASY: SportType.RUNNING,
    ActivityKey.RUN_STRIDES: SportType.RUNNING,
    ActivityKey.RUN_LONG: SportType.RUNNING,
    ActivityKey.RUN_RECOVERY: SportType.RUNNING,
    ActivityKey.RUN_TEMPO: SportType.RUNNING,
    ActivityKey.RUN_THRESHOLD: SportType.RUNNING,
    ActivityKey.RUN_VO2MAX: SportType.RUNNING,
    ActivityKey.RUN_VO2MAX_SHORT: SportType.RUNNING,
    ActivityKey.RUN_PROGRESSION: SportType.RUNNING,
    ActivityKey.RUN_FARTLEK: SportType.RUNNING,
    ActivityKey.RUN_HILL_REPEATS: SportType.RUNNING,
    ActivityKey.RUN_HM_PACE: SportType.RUNNING,
    ActivityKey.RUN_FULL_MP: SportType.RUNNING,
    ActivityKey.RUN_LONG_WITH_MP: SportType.RUNNING,
    ActivityKey.RUN_5K_RACE_PACE: SportType.RUNNING,
    ActivityKey.RUN_10K_RACE_PACE: SportType.RUNNING,
    ActivityKey.RUN_SPEED: SportType.RUNNING,
    ActivityKey.RUN_LONG_PROGRESSION: SportType.RUNNING,
    ActivityKey.RUN_AEROBIC_TEMPO: SportType.RUNNING,
    ActivityKey.RUN_BRICK_SHORT: SportType.RUNNING,
    ActivityKey.RUN_BRICK_LONG: SportType.RUNNING,
    ActivityKey.RUN_BRICK_HIM_TEMPO: SportType.RUNNING,
    ActivityKey.RUN_BRICK_IM_TEMPO: SportType.RUNNING,
    ActivityKey.RUN_THRESHOLD_TEST: SportType.RUNNING,
    ActivityKey.RUN_TRAIL_EASY: SportType.RUNNING,
    ActivityKey.RUN_TRAIL_LONG: SportType.RUNNING,
    ActivityKey.RUN_TRAIL_B2B: SportType.RUNNING,
    ActivityKey.RUN_VERT_HIKE: SportType.RUNNING,
    ActivityKey.RUN_DOWNHILL_REPEATS: SportType.RUNNING,
    # Cycling
    ActivityKey.BIKE_ENDURANCE: SportType.CYCLING,
    ActivityKey.BIKE_LONG: SportType.CYCLING,
    ActivityKey.BIKE_LONG_SWEET_SPOT: SportType.CYCLING,
    ActivityKey.BIKE_LONG_HIM_RACE_PACE: SportType.CYCLING,
    ActivityKey.BIKE_LONG_IM_RACE_PACE: SportType.CYCLING,
    ActivityKey.BIKE_RECOVERY: SportType.CYCLING,
    ActivityKey.BIKE_SWEET_SPOT: SportType.CYCLING,
    ActivityKey.BIKE_THRESHOLD: SportType.CYCLING,
    ActivityKey.BIKE_VO2MAX: SportType.CYCLING,
    ActivityKey.BIKE_VO2MAX_SHORT: SportType.CYCLING,
    ActivityKey.BIKE_TEMPO: SportType.CYCLING,
    ActivityKey.BIKE_CADENCE: SportType.CYCLING,
    ActivityKey.BIKE_ANAEROBIC: SportType.CYCLING,
    ActivityKey.BIKE_SPRINT: SportType.CYCLING,
    ActivityKey.BIKE_HILL_CLIMB: SportType.CYCLING,
    ActivityKey.BIKE_OVER_UNDER: SportType.CYCLING,
    ActivityKey.BIKE_AEROBIC: SportType.CYCLING,
    ActivityKey.BIKE_OPENER: SportType.CYCLING,
    ActivityKey.BIKE_FTP_TEST: SportType.CYCLING,
    # Swimming
    ActivityKey.SWIM_ENDURANCE: SportType.SWIMMING,
    ActivityKey.SWIM_LONG: SportType.SWIMMING,
    ActivityKey.SWIM_RECOVERY: SportType.SWIMMING,
    ActivityKey.SWIM_CSS: SportType.SWIMMING,
    ActivityKey.SWIM_CSS_SHORT: SportType.SWIMMING,
    ActivityKey.SWIM_VO2MAX: SportType.SWIMMING,
    ActivityKey.SWIM_VO2MAX_SHORT: SportType.SWIMMING,
    ActivityKey.SWIM_THRESHOLD: SportType.SWIMMING,
    ActivityKey.SWIM_DRILL: SportType.SWIMMING,
    ActivityKey.SWIM_PULL: SportType.SWIMMING,
    ActivityKey.SWIM_SPRINT: SportType.SWIMMING,
    ActivityKey.SWIM_KICK: SportType.SWIMMING,
    ActivityKey.SWIM_CSS_TEST: SportType.SWIMMING,
}


def sport_of(key: ActivityKey) -> SportType:
    """Return the sport associated with an activity key."""
    return _SPORT_BY_KEY[key]


# Violation and error types.


@dataclass(frozen=True, slots=True)
class ContractViolation:
    """One detected contract violation."""

    goal: GoalType
    phase: PhaseType
    mode_key: str
    ambition: AmbitionLevel | None
    rule: str
    detail: str

    def format(self) -> str:
        """Format the violation as a single human-readable line."""
        amb = self.ambition.value if self.ambition is not None else "*"
        return (
            f"[{self.goal.value} {self.phase.value} "
            f"{self.mode_key} {amb}] {self.rule}: {self.detail}"
        )


class MenuContractError(Exception):
    """Raised at import time when one or more contracts fail."""

    def __init__(self, violations: list[ContractViolation]) -> None:
        """Store violations and build a multi-line error message."""
        self.violations = violations
        body = "\n  ".join(v.format() for v in violations)
        super().__init__(f"{len(violations)} menu contract violation(s):\n  {body}")


# Validation passes.


def _variants_for_ambition(
    slots: Iterable[Slot], ambition: AmbitionLevel
) -> list[tuple[Slot, Variant]]:
    """Return ``(slot, variant)`` pairs that fire for *ambition*."""
    return [
        (slot, variant)
        for slot in slots
        for variant in slot.variants
        if ambition in bounds_ambitions(variant.bounds)
    ]


def _check_phase_menu(goal: GoalType, menu: PhaseMenu) -> list[ContractViolation]:
    """Apply the contract on *menu* across every ambition tier."""
    out: list[ContractViolation] = []
    contract = menu.contract

    # Slot names are provenance: the dossier renders them so reviewers
    # can jump from an emitted session to the authoring slot. Duplicates
    # within one menu would make that mapping ambiguous.
    seen: set[str] = set()
    for slot in menu.slots:
        if slot.name in seen:
            out.append(
                ContractViolation(
                    goal=goal,
                    phase=menu.phase,
                    mode_key=menu.mode_key,
                    ambition=None,
                    rule="slot_name_unique",
                    detail=f"duplicate slot name {slot.name!r}",
                )
            )
        seen.add(slot.name)

    # must_contain / must_not_contain are tier-independent: they apply
    # to the union of activity keys across every Variant on the menu.
    all_keys: set[ActivityKey] = set()
    for slot in menu.slots:
        for variant in slot.variants:
            all_keys.add(variant.activity_key)
            all_keys.update(variant.rotation)

    out.extend(
        ContractViolation(
            goal=goal,
            phase=menu.phase,
            mode_key=menu.mode_key,
            ambition=None,
            rule="must_contain",
            detail=f"required activity {required.value!r} is absent",
        )
        for required in contract.must_contain
        if required not in all_keys
    )
    out.extend(
        ContractViolation(
            goal=goal,
            phase=menu.phase,
            mode_key=menu.mode_key,
            ambition=None,
            rule="must_not_contain",
            detail=f"forbidden activity {forbidden.value!r} is present",
        )
        for forbidden in contract.must_not_contain
        if forbidden in all_keys
    )

    # Race-week menus are authored around a Sunday race: no slot may sit
    # on the assumed race day. Non-Sunday races are a runtime concern —
    # the day assigner evicts sessions colliding with the actual race day.
    if menu.mode_key == "race":
        out.extend(
            ContractViolation(
                goal=goal,
                phase=menu.phase,
                mode_key=menu.mode_key,
                ambition=None,
                rule="race_menu_sunday",
                detail=(
                    f"race-week slot {slot.name!r} is authored on Sunday "
                    f"(race menus assume a Sunday race)"
                ),
            )
            for slot in menu.slots
            if slot.day == Weekday.SUN
        )

    # Per-tier checks: every ambition that has at least one Variant
    # firing for it gets evaluated.
    tiers = sorted(
        {
            ambition
            for slot in menu.slots
            for variant in slot.variants
            for ambition in bounds_ambitions(variant.bounds)
        },
        key=lambda a: a.value,
    )
    for ambition in tiers:
        pairs = _variants_for_ambition(menu.slots, ambition)
        out.extend(_check_tier(goal, menu, contract, ambition, pairs))
        out.extend(_check_tier_days(goal, menu, ambition))

    return out


def _check_tier(
    goal: GoalType,
    menu: PhaseMenu,
    contract: MenuContract,
    ambition: AmbitionLevel,
    pairs: list[tuple[Slot, Variant]],
) -> list[ContractViolation]:
    """Apply per-tier contract checks."""
    out: list[ContractViolation] = []

    # Per-sport session count. Counts each Variant once regardless of
    # whether it's wrapped in a slot_group — the day-layout lints
    # (_check_tier_days) replay the runtime slot-group resolution for
    # the rules where over-counting would matter.
    sport_counts: dict[SportType, int] = {}
    for _slot, variant in pairs:
        sport = sport_of(variant.activity_key)
        sport_counts[sport] = sport_counts.get(sport, 0) + 1
    for sport, minimum in contract.min_sport_sessions.items():
        got = sport_counts.get(sport, 0)
        if got < minimum:
            out.append(
                ContractViolation(
                    goal=goal,
                    phase=menu.phase,
                    mode_key=menu.mode_key,
                    ambition=ambition,
                    rule="min_sport_sessions",
                    detail=(f"{sport.value}: requires {minimum}, found {got}"),
                )
            )

    # Swim floor: a load or taper swim never starts under the tier's least.
    for slot, variant in pairs:
        sport = sport_of(variant.activity_key)
        floor = max(
            swim_floor_metres(sport, key, menu.mode_key, ambition)
            for key in (variant.activity_key, *variant.rotation)
        )
        bounds = resolve_bounds(variant.bounds, ambition)
        low = bounds[0] if bounds is not None else floor
        if low < floor:
            out.append(
                ContractViolation(
                    goal=goal,
                    phase=menu.phase,
                    mode_key=menu.mode_key,
                    ambition=ambition,
                    rule="swim_floor",
                    detail=f"{slot.name!r} starts at {low} m, under {floor} m",
                )
            )

    # Brick count per week.
    if contract.bricks_per_week is not None:
        lo, hi = contract.bricks_per_week
        bricks = sum(1 for _, v in pairs if v.is_brick_pair)
        if not (lo <= bricks <= hi):
            out.append(
                ContractViolation(
                    goal=goal,
                    phase=menu.phase,
                    mode_key=menu.mode_key,
                    ambition=ambition,
                    rule="bricks_per_week",
                    detail=(f"requires {lo}-{hi}, found {bricks}"),
                )
            )

    # Weekly quality-session count (Z4+ — THRESHOLD + INTERVAL families),
    # classified via the planner ActivityRegistry.
    if contract.weekly_quality_sessions is not None:
        lo, hi = contract.weekly_quality_sessions
        quality = sum(1 for _, v in pairs if _is_quality(v.activity_key))
        if not (lo <= quality <= hi):
            out.append(
                ContractViolation(
                    goal=goal,
                    phase=menu.phase,
                    mode_key=menu.mode_key,
                    ambition=ambition,
                    rule="weekly_quality_sessions",
                    detail=(f"requires {lo}-{hi}, found {quality}"),
                )
            )

    return out


# Day-layout lints (per tier). The menus author the calendar: every
# slot's day is the placement. These lints replay the runtime weekly
# instantiation (frac filter + slot-group resolution from menus.resolve)
# so they see exactly the layouts the generator emits — naive slot-pair
# counting would false-positive on frac crossovers and slot-group
# alternatives.

#: Hard ceiling on sessions authored per day (tri doubles are common;
#: triples are never intended).
_MAX_SESSIONS_PER_DAY = 2


def _frac_samples(specs: Iterable[SessionSpec]) -> list[float]:
    """Sample points covering every distinct weekly layout of *specs*.

    Layouts are piecewise-constant in ``phase_frac`` and change at two
    kinds of point: a spec's window boundary (it starts/stops firing),
    and — within a slot group — the Voronoi boundary between two
    members' window midpoints, where the resolver's nearest-midpoint
    winner flips (with 3+ concurrently-active members this can happen
    strictly inside every window). Sampling all boundaries of both
    kinds plus the midpoints between consecutive sample points visits
    every layout the generator can emit, including inclusive-boundary
    co-fires at the breakpoints themselves.
    """
    points = {0.0, 1.0}
    group_mids: dict[str, list[float]] = {}
    for s in specs:
        if s.race_sim:
            continue
        points.add(s.phase_fraction_start)
        points.add(s.phase_fraction_end)
        if s.slot_group is not None:
            group_mids.setdefault(s.slot_group, []).append(
                (s.phase_fraction_start + s.phase_fraction_end) / 2
            )
    for mids_of_group in group_mids.values():
        for i, mid_a in enumerate(mids_of_group):
            for mid_b in mids_of_group[i + 1 :]:
                points.add((mid_a + mid_b) / 2)
    bps = sorted(p for p in points if 0.0 <= p <= 1.0)
    mids = [(a + b) / 2 for a, b in pairwise(bps)]
    return sorted(set(bps) | set(mids))


def _is_hard_spec(spec: SessionSpec) -> bool:
    """True when *spec* can fire a physiologically hard session.

    Rotation-aware: rotated-in activity keys fire on real weeks, so a
    spec is hard when its activity_key or any rotation member is hard.
    Uses ``hardness.is_hard_physiologically`` — the signal hard/easy
    alternation is defined over (MODERATE-hardness sessions like tempo
    runs deliberately don't count).
    """
    # Local import to keep menus.contracts free of registry import
    # side-effects when only validate_blueprint() is in play.
    from endury.planner.activity.registry import (
        ensure_registry_loaded,
        get_activity,
    )

    ensure_registry_loaded()

    def hard(key: ActivityKey) -> bool:
        try:
            return bool(get_activity(key).hardness.is_hard_physiologically)
        except KeyError:  # pragma: no cover — defensive: every key is registered
            return False

    return any(hard(k) for k in (spec.activity_key, *spec.rotation))


#: Goals whose weeks are multi-sport: the run_long_after_hard_run_day rule
#: applies only here (see _check_tier_days docstring).
_TRIATHLON_GOALS: frozenset[GoalType] = frozenset(
    {
        GoalType.SPRINT_TRIATHLON,
        GoalType.OLYMPIC_TRIATHLON,
        GoalType.HALF_IRONMAN,
        GoalType.IRONMAN,
        GoalType.TRIATHLON_MAINTENANCE,
    }
)

#: Run families that count as "an intensive run" for the long-run spacing
#: rule. Broader than hard_adjacent_days' physiological-hardness signal on
#: purpose: a tempo run is moderate for alternation, but still carries
#: fatigue the long run should not inherit.
_HARD_RUN_FAMILIES: frozenset[ActivityFamily] = frozenset(
    {
        ActivityFamily.TEMPO,
        ActivityFamily.THRESHOLD,
        ActivityFamily.INTERVAL,
        ActivityFamily.TEST,
    }
)


def _fires_run_family(spec: SessionSpec, families: frozenset[ActivityFamily]) -> bool:
    """True when *spec* can fire a running session in one of *families*.

    Rotation-aware, like ``_is_hard_spec``: any rotation member counts.
    """
    from endury.planner.activity.registry import (
        ensure_registry_loaded,
        get_activity,
    )

    ensure_registry_loaded()

    def matches(key: ActivityKey) -> bool:
        try:
            activity = get_activity(key)
        except KeyError:  # pragma: no cover — defensive: every key is registered
            return False
        return activity.sport == SportType.RUNNING and activity.family in families

    return any(matches(k) for k in (spec.activity_key, *spec.rotation))


def _check_tier_days(
    goal: GoalType,
    menu: PhaseMenu,
    ambition: AmbitionLevel,
) -> list[ContractViolation]:
    """Day-layout lints on every weekly layout *menu* emits for *ambition*.

    Rules: at most ``_MAX_SESSIONS_PER_DAY`` sessions per day
    (``day_capacity``), at most one session per (day, sport)
    (``same_sport_same_day``), no same-sport physiologically hard
    sessions on adjacent days (``hard_adjacent_days``), every brick
    variant shares its day with a cycling session
    (``brick_partner_day``), and — triathlon goals only — no long run
    the day after an intensive run session
    (``run_long_after_hard_run_day``): multi-sport weeks can place run
    intensity on non-run days, so the long run never needs to sit on
    impact-fatigued legs. Run-only plans stack quality before the long
    run deliberately (accumulated-fatigue training), so they are exempt.
    """
    # Local import: resolve imports this module for sport_of, so the
    # dependency back-edge must stay function-local.
    from endury.planner.sessions.menus.resolve import active_specs, flatten_menu

    # Non-empty by construction: the caller only passes ambitions with
    # at least one firing Variant on this menu.
    specs = flatten_menu(menu, ambition)

    # The same violation typically recurs across many frac samples;
    # key by (rule, detail) so each distinct defect reports once.
    seen: dict[tuple[str, str], ContractViolation] = {}

    def add(rule: str, detail: str) -> None:
        seen.setdefault(
            (rule, detail),
            ContractViolation(
                goal=goal,
                phase=menu.phase,
                mode_key=menu.mode_key,
                ambition=ambition,
                rule=rule,
                detail=detail,
            ),
        )

    def day_name(day: int) -> str:
        return Weekday(day).name.lower()

    # A cadence spec fires on its own weeks, so every layout is replayed
    # with no interval firing and with each interval the menu names firing.
    cadences = [frozenset[int]()] + [
        frozenset({every})
        for every in sorted({s.cadence_weeks for s in specs if s.cadence_weeks})
    ]
    # A load_weeks spec reads its own place in a LOAD week, so its layouts
    # replay against every place the calendar specs can read.
    loads: list[float | None] = (
        list(_frac_samples(specs)) if any(s.load_weeks for s in specs) else [None]
    )
    for is_sim_week in (False, True):
        for frac, load, cadence in product(_frac_samples(specs), loads, cadences):
            layout = active_specs(list(specs), frac, is_sim_week, cadence, load)

            by_day: dict[int, list[SessionSpec]] = {}
            for s in layout:
                by_day.setdefault(s.day_hint, []).append(s)

            for day, members in sorted(by_day.items()):
                if len(members) > _MAX_SESSIONS_PER_DAY:
                    add(
                        "day_capacity",
                        f"{len(members)} sessions on {day_name(day)} "
                        f"(max {_MAX_SESSIONS_PER_DAY})",
                    )
                sport_counts: dict[SportType, int] = {}
                for s in members:
                    sport_counts[s.sport] = sport_counts.get(s.sport, 0) + 1
                for sport, n in sorted(sport_counts.items()):
                    if n > 1:
                        add(
                            "same_sport_same_day",
                            f"{n} {sport.value} sessions on {day_name(day)}",
                        )

            hard_days: dict[SportType, set[int]] = {}
            for s in layout:
                if _is_hard_spec(s):
                    hard_days.setdefault(s.sport, set()).add(s.day_hint)
            for sport, days in sorted(hard_days.items()):
                # Modular adjacency: consecutive LOAD weeks repeat the
                # same layout, so an authored Sunday is calendar-adjacent
                # to the layout's own Monday in the next week.
                for d in sorted(days):
                    nxt = (d + 1) % 7
                    if nxt in days:
                        add(
                            "hard_adjacent_days",
                            f"{sport.value} hard sessions on adjacent days "
                            f"{day_name(d)}+{day_name(nxt)}",
                        )

            cycling_days = {s.day_hint for s in layout if s.sport == SportType.CYCLING}
            for s in layout:
                if s.is_brick_pair and s.day_hint not in cycling_days:
                    add(
                        "brick_partner_day",
                        f"brick {s.activity_key.value} on "
                        f"{day_name(s.day_hint)} has no same-day ride",
                    )

            if goal in _TRIATHLON_GOALS:
                # A brick run off the long ride is the weekend's own fatigue:
                # the long run the next day is written for it (the ride,
                # brick, long run weekend of a triathlon plan).
                intense_run_days = {
                    s.day_hint
                    for s in layout
                    if _fires_run_family(s, _HARD_RUN_FAMILIES) and not s.is_brick_pair
                }
                long_run_days = {
                    s.day_hint
                    for s in layout
                    if _fires_run_family(s, frozenset({ActivityFamily.LONG}))
                }
                for d in sorted(intense_run_days):
                    # Modular adjacency, like hard_adjacent_days above.
                    if (d + 1) % 7 in long_run_days:
                        add(
                            "run_long_after_hard_run_day",
                            f"long run on {day_name((d + 1) % 7)} follows an "
                            f"intensive run on {day_name(d)}",
                        )

    return list(seen.values())


_QUALITY_FAMILIES = frozenset({"threshold", "interval"})


def _is_quality(activity_key: ActivityKey) -> bool:
    """Return True when *activity_key* belongs to a Z4+ family.

    Resolved against the planner ActivityRegistry (the single
    session-classification authority).
    Activities outside the registry default to non-quality so contract
    enforcement degrades gracefully — the import-time validator still
    raises on must_contain/must_not_contain violations.
    """
    # Local import to keep menus.contracts free of registry import
    # side-effects when only validate_blueprint() is in play.
    from endury.planner.activity.registry import (
        ensure_registry_loaded,
        get_activity,
    )

    ensure_registry_loaded()
    try:
        return get_activity(activity_key).family.value in _QUALITY_FAMILIES
    except KeyError:  # pragma: no cover — defensive: every ActivityKey is registered
        return False


# Public API.

_EXPECTED_COVERAGE: frozenset[tuple[PhaseType, str]] = frozenset(
    {
        (PhaseType.BASE, "load"),
        (PhaseType.BASE, "recovery"),
        (PhaseType.BUILD, "load"),
        (PhaseType.BUILD, "recovery"),
        (PhaseType.PEAK, "load"),
        (PhaseType.PEAK, "recovery"),
        (PhaseType.TAPER, "taper"),
        (PhaseType.TAPER, "race"),
    }
)

#: Maintenance goals emit an all-MAINTAIN skeleton with only LOAD and
#: RECOVERY week modes — no taper or race menus exist.
_MAINTENANCE_COVERAGE: frozenset[tuple[PhaseType, str]] = frozenset(
    {
        (PhaseType.MAINTAIN, "load"),
        (PhaseType.MAINTAIN, "recovery"),
    }
)


def expected_coverage(goal: GoalType) -> frozenset[tuple[PhaseType, str]]:
    """Return the (phase, mode_key) coverage matrix required for *goal*."""
    if is_maintenance_goal(goal):
        return _MAINTENANCE_COVERAGE
    return _EXPECTED_COVERAGE


def validate_blueprint(blueprint: PlanBlueprint) -> list[ContractViolation]:
    """Validate one blueprint and raise on the first set of violations.

    Args:
        blueprint: The blueprint to check.

    Returns:
        Empty list on success. The list is returned for callers that
        want to inspect violations without raising; the canonical entry
        point at the bottom of each ``<goal>.py`` lets the raise fire.

    Raises:
        MenuContractError: When one or more violations are found.
    """
    violations: list[ContractViolation] = []

    coverage = expected_coverage(blueprint.goal)
    missing = coverage - set(blueprint.phases.keys())
    extra = set(blueprint.phases.keys()) - coverage
    if missing:
        violations.append(
            ContractViolation(
                goal=blueprint.goal,
                phase=PhaseType.BASE,
                mode_key="*",
                ambition=None,
                rule="coverage",
                detail=(
                    f"missing (phase, mode_key) entries: "
                    f"{sorted((p.value, m) for p, m in missing)}"
                ),
            )
        )
    if extra:
        violations.append(
            ContractViolation(
                goal=blueprint.goal,
                phase=PhaseType.BASE,
                mode_key="*",
                ambition=None,
                rule="coverage",
                detail=(
                    f"unexpected (phase, mode_key) entries: "
                    f"{sorted((p.value, m) for p, m in extra)}"
                ),
            )
        )

    for (phase, mode_key), menu in blueprint.phases.items():
        if menu.phase != phase or menu.mode_key != mode_key:
            violations.append(
                ContractViolation(
                    goal=blueprint.goal,
                    phase=phase,
                    mode_key=mode_key,
                    ambition=None,
                    rule="coverage",
                    detail=(
                        f"PhaseMenu phase/mode_key mismatch: stored "
                        f"({menu.phase.value}, {menu.mode_key!r})"
                    ),
                )
            )
        violations.extend(_check_phase_menu(blueprint.goal, menu))

    if violations:
        raise MenuContractError(violations)
    return violations


def validate_all(
    blueprints: Mapping[GoalType, PlanBlueprint],
) -> list[ContractViolation]:
    """Run cross-blueprint invariants on every blueprint in *blueprints*.

    Today this is a thin wrapper that calls :func:`validate_blueprint`
    on each entry. Cross-blueprint rules (e.g. "every triathlon goal
    must contain the same swim-CSS activity") will land here.

    Args:
        blueprints: The registry to validate.

    Returns:
        Empty list on success.

    Raises:
        MenuContractError: On the first set of violations encountered.
    """
    all_violations: list[ContractViolation] = []
    for blueprint in blueprints.values():
        try:
            validate_blueprint(blueprint)
        except MenuContractError as exc:
            all_violations.extend(exc.violations)
    if all_violations:
        raise MenuContractError(all_violations)
    return all_violations


__all__ = [
    "ContractViolation",
    "MenuContractError",
    "expected_coverage",
    "sport_of",
    "validate_all",
    "validate_blueprint",
]
