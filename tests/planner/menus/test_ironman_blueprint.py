"""Tests for the Ironman blueprint and the menus.__init__ adapter."""

from __future__ import annotations

from itertools import product

import pytest

from endury.planner.foundations.types import (
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    SportType,
)
from endury.planner.sessions.menus import (
    BLUEPRINTS,
    MAINTENANCE_MENUS,
    SessionSpec,
    get_session_menu,
)
from endury.planner.sessions.menus.contracts import expected_coverage
from endury.planner.sessions.menus.resolve import active_specs


def _cadences(specs) -> list[frozenset[int]]:
    """No cadence firing, then each interval the specs name firing."""
    every = sorted({s.cadence_weeks for s in specs if s.cadence_weeks})
    return [frozenset()] + [frozenset({n}) for n in every]


_PHASE_MODES = (
    (PhaseType.BASE, "load"),
    (PhaseType.BASE, "recovery"),
    (PhaseType.BUILD, "load"),
    (PhaseType.BUILD, "recovery"),
    (PhaseType.PEAK, "load"),
    (PhaseType.PEAK, "recovery"),
    (PhaseType.TAPER, "taper"),
    (PhaseType.TAPER, "race"),
)


def _spec_key(spec: SessionSpec) -> tuple:
    return (
        spec.activity_key,
        spec.sport,
        spec.day_hint,
        round(spec.phase_fraction_start, 4),
        round(spec.phase_fraction_end, 4),
        spec.slot_group,
        spec.is_brick_pair,
        spec.rotation,
    )


def test_blueprints_registry_contains_every_goal():
    expected = {
        GoalType.IRONMAN,
        GoalType.HALF_IRONMAN,
        GoalType.OLYMPIC_TRIATHLON,
        GoalType.SPRINT_TRIATHLON,
        GoalType.TRAIL_100K,
        GoalType.TRAIL_50K,
        GoalType.TRAIL_20K,
        GoalType.RUNNING_MARATHON,
        GoalType.RUNNING_HALF_MARATHON,
        GoalType.RUNNING_10K,
        GoalType.RUNNING_5K,
        GoalType.RUNNING_MAINTENANCE,
        GoalType.TRAIL_MAINTENANCE,
        GoalType.TRIATHLON_MAINTENANCE,
    }
    assert set(BLUEPRINTS) == expected


def test_ironman_blueprint_covers_every_phase_mode_combo():
    keys = set(BLUEPRINTS[GoalType.IRONMAN].phases.keys())
    assert keys == set(_PHASE_MODES)


def test_maintenance_menus_cover_triathlon_goals():
    for goal in (
        GoalType.IRONMAN,
        GoalType.HALF_IRONMAN,
        GoalType.OLYMPIC_TRIATHLON,
        GoalType.SPRINT_TRIATHLON,
    ):
        assert goal in MAINTENANCE_MENUS


_GOAL_COVERAGE = [
    (goal, phase, mode_key)
    for goal in sorted(BLUEPRINTS, key=lambda g: g.value)
    for phase, mode_key in sorted(
        expected_coverage(goal), key=lambda pm: (pm[0].value, pm[1])
    )
]


@pytest.mark.parametrize(
    ("goal", "phase", "mode_key"),
    _GOAL_COVERAGE,
    ids=[f"{g.value}/{p.value}/{m}" for g, p, m in _GOAL_COVERAGE],
)
@pytest.mark.parametrize(
    "ambition",
    list(AmbitionLevel),
    ids=lambda a: a.value,
)
def test_blueprint_emits_non_empty_menu(goal, phase, mode_key, ambition):
    """Every blueprint must serve at least one spec for every tier/phase/mode.

    The (phase, mode_key) pairs are derived per-goal from
    ``expected_coverage`` — maintenance blueprints only ever cover the two
    MAINTAIN pairs, while race blueprints keep the 8 classic pairs.
    """
    specs = get_session_menu(goal, phase, mode_key, ambition)
    assert specs, (
        f"empty menu for {goal.value}/{ambition.value}/{phase.value}/{mode_key}"
    )


def test_import_time_validate_blueprint_raises_on_broken_input():
    """A broken blueprint must raise MenuContractError at validate time."""
    from endury.planner.sessions.menus.contracts import (
        MenuContractError,
        validate_blueprint,
    )
    from endury.planner.sessions.menus.types import (
        MenuContract,
        PhaseMenu,
        PlanBlueprint,
        Weekday,
    )
    from tests.planner.menus.helpers import session

    broken_menu = PhaseMenu(
        phase=PhaseType.BUILD,
        mode_key="load",
        slots=(
            session(
                "evil",
                Weekday.WED,
                ActivityKey.RUN_5K_RACE_PACE,
                (30, 45),
            ),
        ),
        contract=MenuContract(
            must_not_contain=frozenset({ActivityKey.RUN_5K_RACE_PACE})
        ),
    )
    blueprint = PlanBlueprint(
        goal=GoalType.IRONMAN,
        phases={(PhaseType.BUILD, "load"): broken_menu},
    )
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    rules = {v.rule for v in exc_info.value.violations}
    assert "coverage" in rules
    assert "must_not_contain" in rules


_BRICK_PROBE_FRACS = (0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0)


def test_brick_partner_is_colocated_bike_for_every_goal_and_week_shape():
    """A brick's runtime partner must be a same-day bike, at every frac.

    The generator wires a brick DAY-FIRST: the cycling spec sharing the
    brick's ``day_hint`` in the *resolved* weekly menu (post frac-filter,
    post slot-group resolution) is the partner, with the most-recent-
    preceding cycling spec only as a fallback — slot order alone can
    mis-pair a slot-group brick, because group winners resolve after
    every ungrouped slot. This test re-runs that selection at
    representative phase fractions (race-sim week on and off) and asserts
    a same-day partner exists for every brick in every resolved week
    shape, so a layout change that strands a brick without its co-located
    ride fails loudly.
    """
    for goal, blueprint in BLUEPRINTS.items():
        for phase, mode_key in blueprint.phases:
            for ambition in AmbitionLevel:
                specs = get_session_menu(goal, phase, mode_key, ambition)
                for frac in _BRICK_PROBE_FRACS:
                    for sim_week, cadence in product((False, True), _cadences(specs)):
                        active = list(
                            active_specs(list(specs), frac, sim_week, cadence)
                        )
                        bike_days = {
                            s.day_hint for s in active if s.sport == SportType.CYCLING
                        }
                        for spec in active:
                            if spec.is_brick_pair:
                                ctx = (
                                    f"{goal.value}/{phase.value}/{mode_key}/"
                                    f"{ambition.value} frac={frac} sim={sim_week}"
                                )
                                assert spec.day_hint in bike_days, (
                                    f"brick {spec.activity_key.value} "
                                    f"(day {spec.day_hint}) has no same-day "
                                    f"bike to pair with [{ctx}]"
                                )


def test_race_sim_scope_is_pinned():
    """Pin every variant that carries race_sim=True, registry-wide.

    Ironman: the BASIC big PEAK weekend, and at every tier the
    race-distance swim of the race-sim week, the one PEAK swim exempt from
    the long-swim step (R-SESS-B-006, R-SESS-013). The MODERATE/COMPETITIVE
    PEAK p1 slots are early-peak full-volume variants, not single
    race-sim weeks — converting them to race_sim moves the pinned
    COMPETITIVE sweep (measured: composite 0.0095 → 0.0564). This pin
    guards the tier scoping decision.

    Trail: one race-simulation weekend per goal
    (docs/research/trail_running.md, The race-simulation "Epic Mountain
    Day" — one to two in the whole build-up), plus the sibling slots that
    week reshapes — the 20K and 50K mid-week climbs reverting to uphill
    work so a descent bout's DOMS window does not sit under the rehearsal
    long run, and the 100K BASIC Sunday raised from a short easy run to
    the cycle's biggest back-to-back.
    """
    sims = sorted(
        (goal.value, slot.name)
        for goal, bp in BLUEPRINTS.items()
        for menu in bp.phases.values()
        for slot in menu.slots
        for variant in slot.variants
        if variant.race_sim
    )
    assert sims == [
        ("ironman", "basic_peak_sat_bike_big"),
        ("ironman", "basic_peak_sun_long_run_big"),
        ("ironman", "peak_fri_swim_race_distance"),
        ("trail_100k", "t100_basic_peak_sun_second_day"),
        ("trail_100k", "t100_peak_sat_race_sim"),
        ("trail_20k", "t20_peak_sun_race_sim"),
        ("trail_20k", "t20_peak_tue_hills_sim"),
        ("trail_50k", "t50_peak_sun_race_sim"),
        ("trail_50k", "t50_peak_tue_hills_sim"),
    ]
