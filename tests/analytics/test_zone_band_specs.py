"""Band-table geometry properties.

The canonical ``*_BAND_SPECS`` in ``endury/training/zones.py`` are the one
source of zone geometry (enforced by the ``zone-spec-single-home``
architecture rule). These tests pin the geometric contracts.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from endury.training.zones import (
    HR_BAND_SPECS,
    PACE_BAND_SPECS,
    POWER_BAND_SPECS,
    SWIM_PACE_BAND_SPECS,
)

ALL_SPECS = {
    "hr": HR_BAND_SPECS,
    "power": POWER_BAND_SPECS,
    "pace": PACE_BAND_SPECS,
    "swim_pace": SWIM_PACE_BAND_SPECS,
}


@pytest.mark.parametrize("metric", sorted(ALL_SPECS))
def test_bands_ascend_in_if_space_with_sequential_zone_numbers(metric):
    """Every table ascends in IF space, zones numbered 1..N."""
    specs = ALL_SPECS[metric]
    assert [s.zone_number for s in specs] == list(range(1, len(specs) + 1))
    for spec in specs:
        assert spec.low_factor < spec.high_factor
    uppers = [s.high_factor for s in specs]
    assert uppers == sorted(uppers)


def test_hr_and_swim_pace_share_zone_boundaries_one_to_five():
    """HR and swim-pace zones 1-5 end at 0.85/0.90/0.95/1.00/1.10.

    The swim table stores the same IF boundaries (its athlete-facing values
    are derived as threshold/IF by the builder), so a given intensity
    factor maps to the same zone number in HR and swim pace.
    """
    shared = (0.85, 0.90, 0.95, 1.00, 1.10)
    assert tuple(s.high_factor for s in HR_BAND_SPECS) == shared
    for spec, expected in zip(SWIM_PACE_BAND_SPECS[:5], shared, strict=False):
        assert spec.high_factor == pytest.approx(expected, abs=1e-3)


def test_run_pace_takes_intervals_icus_default_zones():
    """Friel's run pace zones as intervals.icu ships them: % of threshold speed."""
    uppers = tuple(s.high_factor for s in PACE_BAND_SPECS[:6])
    assert uppers == (0.775, 0.877, 0.943, 1.0, 1.034, 1.115)


@pytest.mark.parametrize("metric", ["hr", "pace", "swim_pace"])
def test_hr_and_pace_bands_are_contiguous(metric):
    """Adjacent bands share their boundary exactly."""
    specs = ALL_SPECS[metric]
    for lower, upper in pairwise(specs):
        assert lower.high_factor == upper.low_factor


def test_power_bands_follow_the_coggan_percent_convention():
    """Power bands step by exactly 0.01 between zones (55% -> 56%).

    The shared-boundary rule read strictly would call this a gap, but it is the standard
    Coggan integer-percent display convention (0-55, 56-75, ...), and the
    runtime zone lookup uses upper bounds only, so no IF value can fall
    between zones. Pinned as-is, deliberately.
    """
    for lower, upper in pairwise(POWER_BAND_SPECS):
        assert upper.low_factor - lower.high_factor == pytest.approx(0.01, abs=1e-9)
