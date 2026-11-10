"""Tests for the per-sport Banister model over intervals.icu load."""

from __future__ import annotations

import math
from datetime import date

import pytest

from endury.intervals.fitness import (
    advance_training_load,
    bucket_by_sport,
    per_sport_ctl_atl,
)
from endury.intervals.models import IntervalsActivity
from endury.planner.foundations.types import SportType

TODAY = date(2026, 8, 5)

CTL_DECAY = math.exp(-1 / 42)
ATL_DECAY = math.exp(-1 / 7)


def _act(
    day: str | None,
    sport_type: str = "Run",
    load: float | None = 100.0,
    seconds: int = 3600,
) -> IntervalsActivity:
    """Build a minimal intervals.icu activity fixture."""
    return IntervalsActivity(
        id=f"i{day}{sport_type}",
        start_date_local=None if day is None else f"{day}T06:00:00",
        type=sport_type,
        moving_time=seconds,
        icu_training_load=load,
        distance=10000.0,
    )


class TestAdvanceTrainingLoad:
    """The single Banister day-step."""

    def test_one_day_from_zero(self) -> None:
        ctl, atl = advance_training_load(0.0, 0.0, 100.0)
        assert ctl == pytest.approx(100 * (1 - CTL_DECAY))
        assert atl == pytest.approx(100 * (1 - ATL_DECAY))

    def test_rest_day_decays(self) -> None:
        ctl, atl = advance_training_load(50.0, 80.0, 0.0)
        assert ctl == pytest.approx(50.0 * CTL_DECAY)
        assert atl == pytest.approx(80.0 * ATL_DECAY)


class TestBucketBySport:
    """Activity-type bucketing."""

    def test_known_types_bucket_by_sport(self) -> None:
        buckets = bucket_by_sport(
            [
                _act("2026-08-01"),
                _act("2026-08-02", "VirtualRide"),
                _act("2026-08-03", "Swim"),
            ]
        )
        assert set(buckets) == {
            SportType.RUNNING,
            SportType.CYCLING,
            SportType.SWIMMING,
        }

    def test_hike_buckets_as_running(self) -> None:
        """Power-hiking is prescribed trail training, not a dropped type."""
        buckets = bucket_by_sport([_act("2026-08-01", "Hike")])
        assert set(buckets) == {SportType.RUNNING}

    def test_a_walk_counts_only_from_thirty_minutes(self) -> None:
        """A short walk is everyday movement, a long one is training."""
        assert bucket_by_sport([_act("2026-08-01", "Walk", seconds=29 * 60)]) == {}
        buckets = bucket_by_sport([_act("2026-08-01", "Walk", seconds=30 * 60)])
        assert set(buckets) == {SportType.RUNNING}

    def test_a_short_hike_still_counts(self) -> None:
        buckets = bucket_by_sport([_act("2026-08-01", "Hike", seconds=15 * 60)])
        assert set(buckets) == {SportType.RUNNING}

    def test_unknown_type_is_dropped(self) -> None:
        assert bucket_by_sport([_act("2026-08-01", "WeightTraining")]) == {}


class TestPerSportCtlAtl:
    """The daily EMA, pinned against hand-computed values."""

    def test_single_activity_five_days_out(self) -> None:
        """100 TSS on day 0 then four rest days, seeded at zero on day 0."""
        result = per_sport_ctl_atl([_act("2026-08-01")], date(2026, 8, 5))

        ctl, atl = result[SportType.RUNNING]
        assert ctl == pytest.approx(14.7)
        assert atl == pytest.approx(52.5)
        # Independent closed-form re-derivation of the same recursion.
        assert ctl == pytest.approx(round(100 * (1 - CTL_DECAY) * CTL_DECAY**4, 1) * 7)
        assert atl == pytest.approx(round(100 * (1 - ATL_DECAY) * ATL_DECAY**4, 1) * 7)

    def test_two_activities_accumulate_on_the_same_day(self) -> None:
        one_big = per_sport_ctl_atl([_act("2026-08-05", load=200.0)], TODAY)
        two_small = per_sport_ctl_atl(
            [_act("2026-08-05", load=100.0), _act("2026-08-05", load=100.0)], TODAY
        )
        assert one_big == two_small

    def test_hike_tss_counts_toward_running_load(self) -> None:
        """A power-hike's TSS feeds RUNNING CTL/ATL, same as a run's."""
        hike = per_sport_ctl_atl([_act("2026-08-05", "Hike", load=100.0)], TODAY)
        run = per_sport_ctl_atl([_act("2026-08-05", "Run", load=100.0)], TODAY)
        assert hike[SportType.RUNNING] == run[SportType.RUNNING]

    def test_sports_are_tracked_separately(self) -> None:
        result = per_sport_ctl_atl(
            [_act("2026-08-05"), _act("2026-08-05", "Ride", load=50.0)], TODAY
        )
        assert result[SportType.RUNNING][0] > result[SportType.CYCLING][0]

    def test_sport_with_no_recorded_load_reports_none_not_zero(self) -> None:
        """A strapless athlete's real training must not read as zero fitness."""
        result = per_sport_ctl_atl([_act("2026-08-05", load=None)], TODAY)
        assert result[SportType.RUNNING] is None

    def test_a_single_recorded_load_still_measures_the_sport(self) -> None:
        """Zero-load days mixed with real loads keep counting as zero."""
        result = per_sport_ctl_atl(
            [_act("2026-08-04", load=None), _act("2026-08-05", load=100.0)], TODAY
        )
        measured = result[SportType.RUNNING]
        assert measured is not None
        assert measured[0] > 0.0

    def test_undated_activity_leaves_the_sport_out(self) -> None:
        assert per_sport_ctl_atl([_act(None)], TODAY) == {}

    def test_activities_after_today_are_not_applied(self) -> None:
        result = per_sport_ctl_atl(
            [_act("2026-08-05", load=100.0), _act("2026-08-09", load=999.0)], TODAY
        )
        assert result[SportType.RUNNING] == pytest.approx(
            per_sport_ctl_atl([_act("2026-08-05", load=100.0)], TODAY)[
                SportType.RUNNING
            ]
        )


def test_activity_start_clock_reads_local_time_or_none():
    from endury.intervals.fitness import activity_start_clock

    assert (
        activity_start_clock(
            IntervalsActivity(id="a", start_date_local="2026-09-04T20:06:26")
        )
        == "20:06"
    )
    assert (
        activity_start_clock(IntervalsActivity(id="a", start_date_local="2026-09-04"))
        is None
    )
    assert activity_start_clock(IntervalsActivity(id="a")) is None
