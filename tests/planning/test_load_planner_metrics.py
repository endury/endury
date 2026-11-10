"""The live load context carries the athlete's own intervals.icu zones."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from endury.intervals.models import AthleteProfile, SportSettingsRow
from endury.planner.foundations.types import SportType
from endury.planning.load_planner_metrics import (
    athlete_zone_sets,
    get_planner_load_context,
)

pytestmark = pytest.mark.planning

RUN_ROW = SportSettingsRow(
    types=["Run"],
    lthr=168,
    threshold_pace=4.0,  # m/s -> 4:10/km
    hr_zones=[141, 150, 158, 167, 185],
    pace_zones=[80, 90, 95, 100, 999],
)
RIDE_ROW = SportSettingsRow(
    types=["Ride"], ftp=310, lthr=156, power_zones=[55, 75, 90, 105, 120, 150, 999]
)
SWIM_ROW = SportSettingsRow(
    types=["Swim"],
    threshold_pace=0.9346,  # m/s -> CSS 107 s/100 m
    pace_zones=[77.5, 87.7, 94.3, 100, 103.4, 111.5, 999],
)


def _repos(*rows: SportSettingsRow) -> SimpleNamespace:
    profile = AthleteProfile(id="i1", sportSettings=list(rows))
    return SimpleNamespace(
        intervals_client=SimpleNamespace(get_athlete=lambda: profile)
    )


def test_running_row_gives_heart_rate_and_pace_sets():
    hr, pace = athlete_zone_sets(SportType.RUNNING, RUN_ROW)
    assert (hr.sport, hr.metric.value, hr.bands[0].high) == (
        "running",
        "heart_rate",
        141,
    )
    assert (pace.metric.value, pace.threshold_label) == ("pace", "Threshold Pace")
    # Zone 2 runs from 90 % down to 80 % of threshold speed: 4:38-5:13/km.
    assert pace.bands[1].low == pytest.approx(4.1667 / 0.90, abs=0.001)
    assert pace.bands[1].high == pytest.approx(4.1667 / 0.80, abs=0.001)


def test_cycling_row_gives_power_in_watts_and_swim_row_css_per_100m():
    (power,) = athlete_zone_sets(SportType.CYCLING, RIDE_ROW)
    assert (power.metric.value, power.bands[1].low, power.bands[1].high) == (
        "power",
        170.5,
        232.5,
    )
    (swim,) = athlete_zone_sets(SportType.SWIMMING, SWIM_ROW)
    assert swim.metric.value == "swim_pace"
    assert swim.bands[2].low == pytest.approx(107.0 / 0.943, abs=0.1)


def test_row_without_zone_bounds_gives_no_set():
    assert athlete_zone_sets(SportType.RUNNING, SportSettingsRow(lthr=168)) == []


def test_context_carries_the_athletes_zones_beside_endurys():
    context = get_planner_load_context(_repos(RUN_ROW, RIDE_ROW, SWIM_ROW))

    assert context is not None
    assert context.athlete_zones is not None
    kept = {(zs.sport, zs.metric.value) for zs in context.athlete_zones.zone_sets}
    # The ride row has an LTHR but no HR bounds, so no cycling HR set.
    assert kept == {
        ("running", "heart_rate"),
        ("running", "pace"),
        ("cycling", "power"),
        ("swimming", "swim_pace"),
    }
    # Endury's own bands still size the prescription.
    assert context.training_zones is not None
    assert context.training_zones.get_hr_zones("running").bands[0].high == 143


def test_context_without_zone_bounds_has_no_athlete_zones():
    context = get_planner_load_context(
        _repos(SportSettingsRow(types=["Run"], lthr=168))
    )

    assert context is not None
    assert context.athlete_zones is None
