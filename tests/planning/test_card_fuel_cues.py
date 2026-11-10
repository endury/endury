"""A card's fuel cue says what the brief sheet says for a session that long."""

from __future__ import annotations

import pytest

from endury.planner.activity.steps import _FLUID_CUE, _carb_cue
from endury.planning.brief.fuel import FUELLING, _during_band


@pytest.mark.parametrize("minutes", [100.0, 150.0, 170.0, 180.0, 240.0, 400.0])
def test_card_carbs_are_the_sheets_band(minutes: float) -> None:
    low, high = _during_band(minutes)["carbs_g_per_h"]
    assert _carb_cue(minutes).startswith(f"{low}-{high} g carbs / h")


def test_card_fluid_is_the_sheets_band() -> None:
    low, high = FUELLING["during"][1]["fluid_ml_per_h"]
    assert _FLUID_CUE.startswith(f"{low}-{high} ml fluid / h")
