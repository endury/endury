"""Tests for the brief sheet's fuel and kit bands (``endury.planning.brief.fuel``)."""

from __future__ import annotations

import pytest

from endury.planner.foundations.types import SportType
from endury.planning.brief.fuel import DASH, fuel_plan, kit_lines


@pytest.mark.parametrize(
    ("minutes", "carbs", "fluid"),
    [
        (60, None, None),
        (75, None, None),
        (120, [30, 60], [400, 700]),
        (180, [60, 80], [400, 700]),
        (240, [60, 90], [400, 700]),
    ],
)
def test_bands_by_session_length(
    minutes: int, carbs: list[int] | None, fluid: list[int] | None
) -> None:
    plan = fuel_plan(minutes, weight_kg=None, is_race=False, temp_c=12)
    assert plan["carbs_g_per_h"] == carbs
    assert plan["fluid_ml_per_h"] == fluid


def test_short_session_needs_no_carbs_and_no_meal_line() -> None:
    plan = fuel_plan(45, weight_kg=70, is_race=False, temp_c=None)
    assert plan["carbs_total_g"] is None
    assert plan["fluid_ml_per_h"] is None
    assert plan["before"] is None
    assert plan["carb_load"] is None
    assert plan["notes"] == [
        "Water is enough for this one; no carbs needed on the way."
    ]


def test_totals_round_to_ten_and_long_band_warns_about_practice() -> None:
    plan = fuel_plan(180, weight_kg=None, is_race=False, temp_c=None)
    assert plan["carbs_total_g"] == [180, 240]
    long_plan = fuel_plan(250, weight_kg=None, is_race=False, temp_c=None)
    assert long_plan["carbs_total_g"] == [250, 380]
    assert long_plan["notes"][0].startswith("Up to 90 g an hour only")


def test_meal_line_shows_grams_only_with_a_weight() -> None:
    with_weight = fuel_plan(120, weight_kg=78, is_race=False, temp_c=None)
    without = fuel_plan(120, weight_kg=None, is_race=False, temp_c=None)
    assert with_weight["before"] == (
        f"A normal meal 2{DASH}3 hours before, 78{DASH}156 g of carbs for your "
        "weight; nothing new on the day."
    )
    assert "g of carbs" not in without["before"]
    assert without["before"].startswith(f"A normal meal 2{DASH}3 hours before")


def test_heat_adds_fluid_and_a_note() -> None:
    plan = fuel_plan(120, weight_kg=None, is_race=False, temp_c=24)
    assert plan["fluid_ml_per_h"] == [600, 900]
    assert plan["notes"][-2].startswith("Recent sessions ran warm")
    mild = fuel_plan(120, weight_kg=None, is_race=False, temp_c=20)
    assert mild["fluid_ml_per_h"] == [400, 700]
    # Water alone stays one line, warm or not.
    short = fuel_plan(60, weight_kg=None, is_race=False, temp_c=24)
    assert short["fluid_ml_per_h"] is None
    assert len(short["notes"]) == 1


def test_carb_load_only_for_a_race_over_150_minutes() -> None:
    race = fuel_plan(200, weight_kg=78, is_race=True, temp_c=None)
    assert race["carb_load"] == {"g_per_kg": [7, 10], "g": [546, 780], "days": 1}
    unweighed = fuel_plan(200, weight_kg=None, is_race=True, temp_c=None)
    assert unweighed["carb_load"] == {"g_per_kg": [7, 10], "g": None, "days": 1}
    assert fuel_plan(150, weight_kg=78, is_race=True, temp_c=None)["carb_load"] is None
    assert fuel_plan(200, weight_kg=78, is_race=False, temp_c=None)["carb_load"] is None


def test_kit_by_sport_and_temperature() -> None:
    run = kit_lines(SportType.RUNNING, 7)
    ride = kit_lines(SportType.CYCLING, 7)
    assert run[0] == "Long sleeve or a shirt with arm warmers"
    assert ride != run
    assert 2 <= len(run) <= 3
    assert kit_lines(SportType.RUNNING, -3)[1] == "Warm gloves and a hat"
    assert kit_lines(SportType.RUNNING, 30)[0].startswith("The lightest shirt")


def test_no_kit_for_a_swim_or_an_unknown_temperature() -> None:
    assert kit_lines(SportType.SWIMMING, 15) == []
    assert kit_lines(SportType.RUNNING, None) == []
