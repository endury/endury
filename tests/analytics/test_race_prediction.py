"""Riegel race-time prediction: pinned numbers and validity guards."""

import pytest

from endury.domain.analysis.race_prediction import (
    RIEGEL_EXPONENT,
    STANDARD_RUNNING_DISTANCES_M,
    extrapolation_ratio,
    predict_race_time,
    predict_standard_distances,
)

# The canonical worked example: a 20:00 5 km.
_5K_M = 5000.0
_5K_S = 20 * 60.0


# The model itself


def test_exponent_default_is_riegels_fitted_value():
    assert RIEGEL_EXPONENT == 1.06


def test_doubling_the_distance_matches_the_closed_form():
    assert predict_race_time(_5K_M, _5K_S, 10000.0) == pytest.approx(_5K_S * 2**1.06)


@pytest.mark.parametrize(
    ("target_m", "expected_s"),
    [
        (10000.0, 2501.9),  # 41:41.9
        (21097.5, 5520.2),  # 1:32:00
        (42195.0, 11509.3),  # 3:11:49
    ],
)
def test_pinned_predictions_from_a_20_minute_5k(target_m, expected_s):
    assert predict_race_time(_5K_M, _5K_S, target_m) == pytest.approx(
        expected_s, abs=0.1
    )


def test_same_distance_returns_the_known_time():
    assert predict_race_time(_5K_M, _5K_S, _5K_M) == pytest.approx(_5K_S)


def test_predicting_downwards_is_faster_than_pro_rata():
    # 3:30 marathon -> 10k. Pro rata would be 12600/4.2195 = 2986 s; Riegel
    # says faster, because shorter races are run harder.
    predicted = predict_race_time(42195.0, 12600.0, 10000.0)
    assert predicted < 12600.0 / 4.2195
    assert predicted == pytest.approx(2739.0, abs=0.1)  # 45:39


def test_lower_exponent_predicts_faster_long_races():
    strong_endurance = predict_race_time(_5K_M, _5K_S, 42195.0, exponent=1.05)
    speed_biased = predict_race_time(_5K_M, _5K_S, 42195.0, exponent=1.08)
    assert strong_endurance < predict_race_time(_5K_M, _5K_S, 42195.0)
    assert speed_biased > predict_race_time(_5K_M, _5K_S, 42195.0)


# Input guards


@pytest.mark.parametrize(
    ("known_m", "known_s", "target_m"),
    [
        (0.0, _5K_S, 10000.0),
        (-1.0, _5K_S, 10000.0),
        (_5K_M, _5K_S, 0.0),
    ],
)
def test_non_positive_distances_raise(known_m, known_s, target_m):
    with pytest.raises(ValueError, match="Distances must be positive"):
        predict_race_time(known_m, known_s, target_m)


@pytest.mark.parametrize("known_s", [0.0, -60.0])
def test_non_positive_time_raises(known_s):
    with pytest.raises(ValueError, match="Known time must be positive"):
        predict_race_time(_5K_M, known_s, 10000.0)


@pytest.mark.parametrize("exponent", [0.99, 1.16])
def test_exponent_outside_the_accepted_range_raises(exponent):
    with pytest.raises(ValueError, match="exponent must be between"):
        predict_race_time(_5K_M, _5K_S, 10000.0, exponent=exponent)


@pytest.mark.parametrize("exponent", [1.0, 1.15])
def test_exponent_bounds_are_inclusive(exponent):
    assert predict_race_time(_5K_M, _5K_S, 10000.0, exponent=exponent) > 0


# Standard-distance helper


def test_standard_distances_cover_the_four_road_races():
    predictions = predict_standard_distances(_5K_M, _5K_S)
    assert set(predictions) == {"5k", "10k", "half_marathon", "marathon"}
    assert set(predictions) == set(STANDARD_RUNNING_DISTANCES_M)


def test_standard_distances_reproduce_the_basis_effort():
    predictions = predict_standard_distances(_5K_M, _5K_S)
    assert predictions["5k"] == pytest.approx(_5K_S)
    assert predictions["10k"] == pytest.approx(2501.9, abs=0.1)


def test_standard_distances_honour_the_exponent():
    faster = predict_standard_distances(_5K_M, _5K_S, exponent=1.05)
    assert faster["marathon"] < predict_standard_distances(_5K_M, _5K_S)["marathon"]


def test_standard_distances_propagate_input_errors():
    with pytest.raises(ValueError, match="Known time must be positive"):
        predict_standard_distances(_5K_M, 0.0)


# Extrapolation reach


def test_extrapolation_ratio_is_one_at_the_known_distance():
    assert extrapolation_ratio(_5K_M, _5K_M) == 1.0


def test_extrapolation_ratio_is_symmetric():
    assert extrapolation_ratio(_5K_M, 42195.0) == pytest.approx(8.439)
    assert extrapolation_ratio(42195.0, _5K_M) == pytest.approx(8.439)
