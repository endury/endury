"""Writing thresholds to every intervals.icu sport-settings row of a sport."""

from __future__ import annotations

import pytest

from endury.intervals.models import AthleteProfile, SportSettingsRow
from endury.intervals.sport_settings import set_thresholds
from endury.planner.foundations.types import SportType
from tests.doubles import FakeIntervals


def _fake() -> FakeIntervals:
    return FakeIntervals(
        athlete=AthleteProfile(
            id="i1",
            sportSettings=[
                SportSettingsRow(id=1, types=["Ride", "GravelRide"], ftp=250),
                SportSettingsRow(id=2, types=["VirtualRide"], ftp=240),
                SportSettingsRow(id=3, types=["Run", "TrailRun"], lthr=165),
                SportSettingsRow(id=4, types=["Hike", "Walk"], lthr=150),
                SportSettingsRow(id=5, types=["Swim"], threshold_pace=1.0),
            ],
        )
    )


def _row(fake: FakeIntervals, row_id: int) -> SportSettingsRow:
    return next(r for r in fake.athlete.sport_settings or [] if r.id == row_id)


def test_an_ftp_reaches_every_ride_row_and_nothing_else():
    fake = _fake()

    types = set_thresholds(fake.client(), SportType.CYCLING, ftp_watts=265)

    assert types == ["Ride", "GravelRide", "VirtualRide"]
    assert [_row(fake, i).ftp for i in (1, 2)] == [265, 265]
    assert _row(fake, 3).ftp is None


def test_a_run_edit_leaves_the_hiking_row_alone():
    fake = _fake()

    set_thresholds(
        fake.client(), SportType.RUNNING, lthr_bpm=170, threshold_pace_sec_per_km=250
    )

    assert _row(fake, 3).lthr == 170
    assert _row(fake, 3).threshold_pace == pytest.approx(4.0)
    assert _row(fake, 4).lthr == 150


def test_swim_css_is_written_as_metres_per_second():
    fake = _fake()

    set_thresholds(fake.client(), SportType.SWIMMING, css_sec_per_100m=100)

    assert _row(fake, 5).threshold_pace == pytest.approx(1.0)
    assert _row(fake, 5).lthr is None


def test_nothing_to_write_is_refused():
    with pytest.raises(ValueError, match="at least one threshold"):
        set_thresholds(_fake().client(), SportType.CYCLING)


def test_a_sport_without_settings_is_refused():
    fake = FakeIntervals(
        athlete=AthleteProfile(
            id="i1", sportSettings=[SportSettingsRow(id=1, types=["Run"])]
        )
    )

    with pytest.raises(ValueError, match="no Swim sport settings"):
        set_thresholds(fake.client(), SportType.SWIMMING, css_sec_per_100m=100)
