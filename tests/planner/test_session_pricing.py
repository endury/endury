"""Tests for per-session hours→TSS pricing (``macro/session_pricing.py``)."""

from __future__ import annotations

import pytest

from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.activity.registry import (
    ACTIVITY_REGISTRY,
    ensure_registry_loaded,
    get_activity,
)
from endury.planner.foundations.types import ActivityKey
from endury.planner.macro.session_pricing import session_tss_per_hour


@pytest.mark.parametrize("hours", [0.0, -0.5])
def test_non_positive_hours_carry_no_load(hours: float) -> None:
    assert session_tss_per_hour(ActivityKey.RUN_EASY, hours) == 0.0


@pytest.mark.parametrize(
    ("key", "hours"),
    [
        (ActivityKey.RUN_EASY, 1.0),
        (ActivityKey.RUN_TEMPO, 0.75),
        (ActivityKey.BIKE_LONG, 3.5),
        (ActivityKey.SWIM_ENDURANCE, 0.8),
        (ActivityKey.RUN_THRESHOLD_TEST, 1.0),
    ],
)
def test_price_times_hours_is_the_card_tss(key: ActivityKey, hours: float) -> None:
    """The QP's session load equals what the materialized card achieves."""
    ensure_registry_loaded()
    act = get_activity(key)
    card = act.achieved_tss(assignment_target_tss(act, hours, None), resolver=None)
    assert session_tss_per_hour(key, hours) * hours == pytest.approx(card)


def test_every_activity_prices_positive_at_one_hour() -> None:
    ensure_registry_loaded()
    assert ACTIVITY_REGISTRY
    for key in ACTIVITY_REGISTRY:
        assert session_tss_per_hour(key, 1.0) > 0, key
