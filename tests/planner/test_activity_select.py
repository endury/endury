"""Tests for the intent -> activity picker (endury.planner.activity.select)."""

from __future__ import annotations

import pytest

from endury.planner.activity import ensure_registry_loaded
from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.activity.select import VALID_INTENTS, pick_for_intent
from endury.planner.foundations.types import SportType


@pytest.fixture(autouse=True)
def _registry() -> None:
    ensure_registry_loaded()


@pytest.mark.parametrize(
    ("sport", "intent", "expected_key"),
    [
        (SportType.RUNNING, "endurance", "run_easy"),
        (SportType.RUNNING, "easy", "run_easy"),
        (SportType.RUNNING, "long", "run_long"),
        (SportType.RUNNING, "recovery", "run_recovery"),
        (SportType.CYCLING, "endurance", "bike_endurance"),
        (SportType.CYCLING, "easy", "bike_endurance"),
        (SportType.SWIMMING, "endurance", "swim_endurance"),
        (SportType.SWIMMING, "easy", "swim_endurance"),
        (SportType.SWIMMING, "recovery", "swim_recovery"),
        (SportType.SWIMMING, "long", "swim_long"),
        (SportType.RUNNING, "strides", "run_strides"),
        (SportType.CYCLING, "strides", "bike_opener"),
        (SportType.RUNNING, "trail", "run_trail_easy"),
        # Seen live: "book a threshold test" built Threshold Intervals.
        (SportType.RUNNING, "test", "run_threshold_test"),
        (SportType.CYCLING, "test", "bike_ftp_test"),
        (SportType.SWIMMING, "test", "swim_css_test"),
    ],
)
def test_pick_for_intent_resolves_the_canonical_activity(
    sport: SportType, intent: str, expected_key: str
) -> None:
    picked = pick_for_intent(sport, intent)
    assert picked is not None
    assert picked.key.value == expected_key


def test_strides_unsupported_for_swimming() -> None:
    """Swimming has no easy+neuromuscular activity — no swim strides."""
    assert pick_for_intent(SportType.SWIMMING, "strides") is None


def test_strides_is_a_valid_intent() -> None:
    assert "strides" in VALID_INTENTS


@pytest.mark.parametrize(
    ("minutes", "expected_key"),
    [
        (35, "bike_vo2max_short"),
        (45, "bike_vo2max_short"),
        (60, "bike_vo2max_short"),
        (75, "bike_vo2max"),
    ],
)
def test_vo2_ride_builds_to_the_length_asked_for(
    minutes: int, expected_key: str
) -> None:
    """A 75-min VO2 ride is the 4-min reps; the 30/15 sets end at an hour."""
    picked = pick_for_intent(SportType.CYCLING, "vo2", minutes)
    assert picked is not None
    assert picked.key.value == expected_key


def test_a_length_no_activity_builds_takes_the_closest() -> None:
    picked = pick_for_intent(SportType.CYCLING, "vo2", 120)
    assert picked is not None
    assert picked.key.value == "bike_vo2max"


def test_without_a_length_the_canonical_activity_stands() -> None:
    picked = pick_for_intent(SportType.CYCLING, "vo2")
    assert picked is not None
    assert picked.key.value == "bike_vo2max_short"


def test_sweet_spot_is_the_sweet_spot_ride_not_threshold() -> None:
    picked = pick_for_intent(SportType.CYCLING, "sweet_spot")
    assert picked is not None
    assert picked.key.value == "bike_sweet_spot"
    assert pick_for_intent(SportType.RUNNING, "sweet_spot") is None


def test_sweet_spot_ride_builds_to_two_hours() -> None:
    picked = pick_for_intent(SportType.CYCLING, "sweet_spot", 120)
    assert picked is not None
    assert picked.key.value == "bike_sweet_spot"
    target = assignment_target_tss(picked, 2.0, None)
    assert picked.duration_minutes_exact(target) == pytest.approx(120, abs=5)


def test_a_trail_run_is_the_trail_long_run_not_the_road_one() -> None:
    """A 2.5 h trail run is the hilly long run, steered by heart rate."""
    picked = pick_for_intent(SportType.RUNNING, "trail", 150)
    assert picked is not None
    assert picked.key.value == "run_trail_long"
    assert pick_for_intent(SportType.CYCLING, "trail") is None


def test_an_easy_trail_hour_is_the_easy_trail_run() -> None:
    """An hour on trails is an easy run over hills, built to the hour."""
    picked = pick_for_intent(SportType.RUNNING, "trail", 60)
    assert picked is not None
    assert picked.key.value == "run_trail_easy"
    assert picked.family.value == "easy"
    assert picked.target_preference.value == "hr_first"
    target = assignment_target_tss(picked, 1.0, None)
    assert picked.duration_minutes_exact(target) == pytest.approx(60, abs=5)


def test_a_plain_easy_run_stays_on_the_road() -> None:
    assert pick_for_intent(SportType.RUNNING, "easy", 60).key.value == "run_easy"
