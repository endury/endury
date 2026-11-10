"""Over-under rides: each rep swings under and over threshold, and the card says so."""

from __future__ import annotations

import pytest

from endury.intervals.workout_dsl import SportThresholds, workout_to_dsl
from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.foundations.types import ActivityKey, SportType


@pytest.fixture(scope="module")
def ride():
    ensure_registry_loaded()
    return get_activity(ActivityKey.BIKE_OVER_UNDER)


def test_a_rep_is_three_cycles_of_two_minutes_under_and_one_over(ride) -> None:
    workout = ride.build(60.0)
    repeat = workout.steps[1]
    assert repeat.iterations == 4
    cycles, recovery = repeat.steps
    assert cycles.iterations == 3
    under, over = cycles.steps
    assert (under.duration.value_seconds, over.duration.value_seconds) == (120, 60)
    assert under.description.startswith("Under: ")
    assert over.description.startswith("Over: ")
    assert recovery.duration.value_seconds == 180


def test_the_card_is_as_long_as_its_segments(ride) -> None:
    minutes = sum(m for m, _ in ride.segments(60.0))
    assert minutes == pytest.approx(ride.duration_minutes_exact(60.0))
    # Two minutes under for every minute over.
    intensities = {f: m for m, f in ride.segments(60.0)}
    assert intensities[0.93] == pytest.approx(2 * intensities[1.05])


def test_a_rest_target_builds_no_intervals(ride) -> None:
    assert ride.segments(0.0) == []
    assert all(step.type != "repeat" for step in ride.build(0.0).steps)


def test_the_export_flattens_the_cycles(ride) -> None:
    dsl = workout_to_dsl(
        ride.build(60.0), SportThresholds(ftp_watts=250), SportType.CYCLING
    )
    assert "4x" in dsl
    assert dsl.count("Under") == 3
    assert dsl.count("Over") == 3
