"""``athlete_zone_set``: the athlete's own zones from intervals.icu's bounds."""

from __future__ import annotations

import math

import pytest

from endury.domain.analysis.zones import athlete_zone_set
from endury.domain.models.zones import ZoneMetric, ZoneModel

pytestmark = pytest.mark.analytics


def test_heart_rate_bounds_are_bpm_with_an_open_zone_one():
    zones = athlete_zone_set(
        sport="running",
        metric=ZoneMetric.HEART_RATE,
        threshold_value=168.0,
        threshold_label="LTHR",
        upper_bounds=[141, 150, 158, 167, 185],
        names=["Recovery", "Aerobic", "Tempo", "Sub", "Super"],
        scale=1.0,
    )
    assert zones.model is ZoneModel.CUSTOM
    assert [(b.zone_number, b.name, b.low, b.high) for b in zones.bands[:2]] == [
        (1, "Recovery", 0.0, 141.0),
        (2, "Aerobic", 141.0, 150.0),
    ]
    assert zones.bands[-1].high == 185.0


def test_power_bounds_scale_from_percent_of_ftp_to_watts():
    zones = athlete_zone_set(
        sport="cycling",
        metric=ZoneMetric.POWER,
        threshold_value=310.0,
        threshold_label="FTP",
        upper_bounds=[55, 75, 90, 105, 120, 150, 999],
        names=None,
        scale=3.1,
    )
    endurance = zones.bands[1]
    assert (endurance.name, endurance.low, endurance.high) == ("Zone 2", 170.5, 232.5)


def test_pace_bounds_invert_and_zone_one_has_no_slow_limit():
    # % of threshold speed: zone 3 spans 87.7-94.3 % of CSS 107 s/100 m.
    zones = athlete_zone_set(
        sport="swimming",
        metric=ZoneMetric.SWIM_PACE,
        threshold_value=107.0,
        threshold_label="CSS",
        upper_bounds=[77.5, 87.7, 94.3, 100.0, 999.0],
        names=["Zone 1", "Zone 2"],  # shorter than the bounds: the rest default
        scale=0.01,
        invert=True,
    )
    assert zones.bands[0].low == pytest.approx(107.0 / 0.775)
    assert math.isinf(zones.bands[0].high)
    assert zones.bands[2].name == "Zone 3"
    assert zones.bands[2].low == pytest.approx(107.0 / 0.943)
    assert zones.bands[2].high == pytest.approx(107.0 / 0.877)
