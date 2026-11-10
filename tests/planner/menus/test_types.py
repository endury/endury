"""Tests for endury.planner.sessions.menus.types."""

from __future__ import annotations

import pytest

from endury.planner.foundations.types import (
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
)
from endury.planner.sessions.menus.types import (
    MenuContract,
    PhaseMenu,
    PlanBlueprint,
    Progression,
    Rationale,
    Slot,
    Variant,
    Weekday,
    bounds_ambitions,
    resolve_bounds,
)

# --- bounds helpers -------------------------------------------------


def test_resolve_bounds_tuple_returns_same_for_all_tiers():
    bounds = (10, 20)
    for tier in AmbitionLevel:
        assert resolve_bounds(bounds, tier) == (10, 20)


def test_resolve_bounds_dict_returns_per_tier_or_none():
    bounds = {AmbitionLevel.MODERATE: (30, 40)}
    assert resolve_bounds(bounds, AmbitionLevel.MODERATE) == (30, 40)
    assert resolve_bounds(bounds, AmbitionLevel.BASIC) is None


def test_bounds_ambitions_tuple_returns_all_tiers():
    assert bounds_ambitions((10, 20)) == frozenset(AmbitionLevel)


def test_bounds_ambitions_dict_returns_keys():
    bounds = {AmbitionLevel.BASIC: (10, 20), AmbitionLevel.MODERATE: (15, 25)}
    assert bounds_ambitions(bounds) == frozenset(
        {AmbitionLevel.BASIC, AmbitionLevel.MODERATE}
    )


# --- Weekday --------------------------------------------------------


def test_weekday_enum_indices_match_python_weekday():
    assert Weekday.MON == 0
    assert Weekday.SUN == 6
    assert int(Weekday.TUE) == 1


# --- Rationale ------------------------------------------------------


def test_rationale_defaults_are_empty():
    r = Rationale()
    assert r.intent == ""
    assert r.citations == ()
    assert r.last_reviewed is None


# --- Variant --------------------------------------------------------


def test_variant_post_init_rejects_invalid_frac_window():
    with pytest.raises(ValueError, match="invalid frac"):
        Variant(
            activity_key=ActivityKey.RUN_EASY,
            bounds=(10, 20),
            frac=(0.6, 0.3),
        )


def test_variant_post_init_rejects_frac_outside_unit_interval():
    with pytest.raises(ValueError, match="invalid frac"):
        Variant(
            activity_key=ActivityKey.RUN_EASY,
            bounds=(10, 20),
            frac=(-0.1, 0.5),
        )


def test_variant_post_init_rejects_reversed_tuple_bounds():
    with pytest.raises(ValueError, match="lo > hi"):
        Variant(activity_key=ActivityKey.RUN_EASY, bounds=(40, 30))


def test_variant_post_init_rejects_empty_per_tier_bounds():
    with pytest.raises(ValueError, match="empty per-tier"):
        Variant(activity_key=ActivityKey.RUN_EASY, bounds={})


def test_variant_post_init_rejects_reversed_per_tier_bound():
    with pytest.raises(ValueError, match="lo > hi"):
        Variant(
            activity_key=ActivityKey.RUN_EASY,
            bounds={AmbitionLevel.BASIC: (40, 30)},
        )


def test_variant_accepts_full_optional_fields():
    progression = Progression(name="dummy")
    v = Variant(
        activity_key=ActivityKey.RUN_LONG,
        bounds={AmbitionLevel.COMPETITIVE: (60, 90)},
        frac=(0.0, 0.5),
        progression=progression,
        is_brick_pair=True,
        rationale=Rationale(intent="test"),
        slot_group="g1",
        rotation=(ActivityKey.RUN_LONG, ActivityKey.RUN_EASY),
    )
    assert v.is_brick_pair is True
    assert v.rotation == (ActivityKey.RUN_LONG, ActivityKey.RUN_EASY)


# --- Slot -----------------------------------------------------------


def test_slot_post_init_rejects_empty_variants():
    with pytest.raises(ValueError, match="no variants"):
        Slot(name="empty", day=Weekday.MON, variants=())


# --- PhaseMenu / PlanBlueprint --------------------------------------


def test_phase_menu_and_blueprint_carry_defaults():
    v = Variant(activity_key=ActivityKey.RUN_EASY, bounds=(10, 20))
    slot = Slot(name="ok", day=Weekday.MON, variants=(v,))
    menu = PhaseMenu(phase=PhaseType.BASE, mode_key="load", slots=(slot,))
    assert isinstance(menu.contract, MenuContract)
    blueprint = PlanBlueprint(
        goal=GoalType.IRONMAN,
        phases={(PhaseType.BASE, "load"): menu},
    )
    assert blueprint.phases[(PhaseType.BASE, "load")] is menu


def test_variant_post_init_rejects_a_cadence_under_two_or_on_a_race_sim():
    for changes in ({"cadence_weeks": 1}, {"cadence_weeks": 8, "race_sim": True}):
        with pytest.raises(ValueError, match="cadence_weeks"):
            Variant(activity_key=ActivityKey.RUN_LONG, bounds=(60, 90), **changes)
