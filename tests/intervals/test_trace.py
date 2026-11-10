"""Tests for endury.intervals.trace — the activity intensity-trace projection."""

from __future__ import annotations

import pytest

from endury.intervals.models import (
    ActivityStream,
    IntervalRow,
    IntervalsActivityDetail,
)
from endury.intervals.trace import (
    _bucket_values,
    _duration_s,
    _elevation,
    _has_data,
    _intervals_metric,
    _round_value,
    _row_pct,
    _scale,
    _zone,
    activity_trace,
    trace_stream_types,
)
from endury.planner.foundations.types import SportType


def _detail(**overrides: object) -> IntervalsActivityDetail:
    return IntervalsActivityDetail(**overrides)  # type: ignore[arg-type]


def _stream(type_: str, data: list[float | None]) -> ActivityStream:
    return ActivityStream(type=type_, data=data)


# trace_stream_types


@pytest.mark.parametrize(
    ("type_", "use_gap", "pool_length", "expected"),
    [
        ("Ride", False, None, ["watts", "heartrate", "altitude"]),
        ("Run", False, None, ["velocity_smooth", "heartrate", "altitude"]),
        (
            "TrailRun",
            True,
            None,
            ["velocity_smooth", "heartrate", "altitude", "ga_velocity"],
        ),
        ("Swim", False, 25.0, []),
        ("Swim", False, None, ["velocity_smooth", "heartrate"]),
        ("OpenWaterSwim", False, None, ["velocity_smooth", "heartrate"]),
        ("WeightTraining", False, None, ["heartrate", "altitude"]),
        (None, False, None, ["heartrate", "altitude"]),
    ],
)
def test_trace_stream_types_per_sport_branch(type_, use_gap, pool_length, expected):
    detail = _detail(type=type_, use_gap_zone_times=use_gap, pool_length=pool_length)
    assert trace_stream_types(detail) == expected


# activity_trace: streams source — cycling power


def test_power_trace_with_zones_and_elevation():
    detail = _detail(
        id="i1",
        type="Ride",
        icu_ftp=250,
        icu_power_zones=[55, 75, 90, 105, 120, 150, 999],
        moving_time=3615,
    )
    streams = [
        _stream("watts", [100.0, 200.0, None, 300.0]),
        _stream("altitude", [388.0, 391.0, 395.0, 420.0]),
    ]

    assert activity_trace(detail, streams) == {
        "activity_id": "i1",
        "source": "streams",
        "metric": "power",
        "grade_adjusted": False,
        "threshold": 250,
        "duration_s": 3615,
        "scale": {"lo": 30, "hi": 120},
        "pct": [40.0, 80.0, None, 120.0],
        "zone": [1, 3, None, 5],
        "value": [100, 200, None, 300],
        "elevation": [388, 391, 395, 420],
    }


def test_power_not_drawable_when_ftp_missing_falls_back_to_hr():
    detail = _detail(id="i1b", type="Ride", icu_ftp=None, lthr=150)
    streams = [
        _stream("watts", [100.0, 200.0]),
        _stream("heartrate", [140.0, 150.0]),
    ]

    result = activity_trace(detail, streams)
    assert result["metric"] == "hr"
    assert result["source"] == "streams"


def test_power_not_drawable_when_watts_stream_absent_falls_back_to_hr():
    detail = _detail(id="i1c", type="Ride", icu_ftp=250, lthr=150)
    streams = [_stream("heartrate", [140.0, 150.0])]

    result = activity_trace(detail, streams)
    assert result["metric"] == "hr"


def test_power_not_drawable_when_watts_stream_has_no_data_falls_back_to_hr():
    detail = _detail(id="i1d", type="Ride", icu_ftp=250, lthr=150)
    streams = [
        _stream("watts", [None, None]),
        _stream("heartrate", [140.0, 150.0]),
    ]

    result = activity_trace(detail, streams)
    assert result["metric"] == "hr"


# activity_trace: streams source — running pace (GAP / fallback)


def test_pace_trace_uses_gap_when_present_with_data():
    detail = _detail(
        id="i2",
        type="Run",
        threshold_pace=3.0,
        pace_zones=[80.0, 90.0, 95.0, 100.0, 999.0],
    )
    streams = [
        _stream("ga_velocity", [2.4, 3.0, None]),
        _stream("velocity_smooth", [2.0, 2.0, 2.0]),  # must be ignored
    ]

    assert activity_trace(detail, streams) == {
        "activity_id": "i2",
        "source": "streams",
        "metric": "pace",
        "grade_adjusted": True,
        "threshold": 3.0,
        "duration_s": 3,  # no moving/elapsed time -> falls back to sample count
        "scale": {"lo": 30, "hi": 110},
        "pct": [80.0, 100.0, None],
        "zone": [1, 4, None],
        "value": [2.4, 3.0, None],
    }


def test_pace_trace_falls_back_to_velocity_smooth_when_ga_absent():
    detail = _detail(id="i3", type="Run", threshold_pace=2.0, pace_zones=None)
    streams = [_stream("velocity_smooth", [1.0, 2.0])]

    assert activity_trace(detail, streams) == {
        "activity_id": "i3",
        "source": "streams",
        "metric": "pace",
        "grade_adjusted": False,
        "threshold": 2.0,
        "duration_s": 2,
        "scale": {"lo": 30, "hi": 110},
        "pct": [50.0, 100.0],
        "zone": [None, None],  # missing zone bounds -> None for every bucket
        "value": [1.0, 2.0],
    }


def test_pace_trace_falls_back_to_velocity_smooth_when_ga_present_but_no_data():
    detail = _detail(
        id="i3b",
        type="Run",
        threshold_pace=2.5,
        pace_zones=[100.0, 999.0],
        moving_time=500,
    )
    streams = [
        _stream("ga_velocity", [None, None]),
        _stream("velocity_smooth", [2.5, 2.5]),
    ]

    assert activity_trace(detail, streams) == {
        "activity_id": "i3b",
        "source": "streams",
        "metric": "pace",
        "grade_adjusted": False,
        "threshold": 2.5,
        "duration_s": 500,
        "scale": {"lo": 30, "hi": 110},
        "pct": [100.0, 100.0],
        "zone": [1, 1],
        "value": [2.5, 2.5],
    }


def test_pace_trace_open_water_swim():
    detail = _detail(
        id="i3c",
        type="OpenWaterSwim",
        pool_length=None,
        threshold_pace=1.2,
        pace_zones=[90.0, 95.0, 100.0, 110.0, 999.0],
    )
    streams = [_stream("velocity_smooth", [1.2, 1.44])]

    assert activity_trace(detail, streams) == {
        "activity_id": "i3c",
        "source": "streams",
        "metric": "pace",
        "grade_adjusted": False,
        "threshold": 1.2,
        "duration_s": 2,
        "scale": {"lo": 30, "hi": 120},
        "pct": [100.0, 120.0],
        "zone": [3, 5],
        "value": [1.2, 1.44],
    }


def test_pace_trace_running_falls_back_to_hr_when_no_threshold_pace():
    # Neither the GAP nor the velocity_smooth branch can produce a candidate
    # without a threshold pace, regardless of what streams are present.
    detail = _detail(id="i3e", type="Run", threshold_pace=None, lthr=150)
    streams = [
        _stream("velocity_smooth", [3.0, 3.0]),
        _stream("heartrate", [140.0, 150.0]),
    ]

    result = activity_trace(detail, streams)
    assert result["metric"] == "hr"


def test_pace_trace_open_water_swim_falls_back_to_hr_when_no_threshold_pace():
    detail = _detail(
        id="i3f", type="OpenWaterSwim", pool_length=None, threshold_pace=None, lthr=150
    )
    streams = [
        _stream("velocity_smooth", [1.2, 1.2]),
        _stream("heartrate", [140.0, 150.0]),
    ]

    result = activity_trace(detail, streams)
    assert result["metric"] == "hr"


def test_pool_swim_never_uses_a_streams_metric_even_if_given_one():
    # A pool swim skips BOTH streams candidates -- pace and the HR
    # fallback -- and goes straight to its interval rows, even if (unlike
    # the route, which never fetches streams for a pool swim) usable
    # streams are handed in.
    detail = _detail(
        id="i3d",
        type="Swim",
        pool_length=25.0,
        threshold_pace=1.0,
        lthr=150,
        icu_intervals=[
            IntervalRow(type="WORK", elapsed_time=60, average_speed=1.0, zone=2)
        ],
    )
    streams = [
        _stream("velocity_smooth", [1.0, 1.0]),
        _stream("heartrate", [140.0, 150.0]),
    ]

    result = activity_trace(detail, streams)
    assert result["source"] == "intervals"


# activity_trace: streams source — HR fallback + bucketing


def test_hr_trace_buckets_uneven_stream_with_nulls_and_zone_edges():
    # 97 samples -> 96 buckets: bucket 95 (the last) merges two raw samples,
    # every other bucket is 1:1. Sample 10 is null (an all-null bucket).
    # Values climb 100..196bpm so zone edges land cleanly against
    # icu_hr_zones=[120, 140, 160, 180, 999] (bpm, not pct, for HR).
    data: list[float | None] = [100.0 + i for i in range(97)]
    data[10] = None
    detail = _detail(
        id="i4",
        type="WeightTraining",  # unmapped sport -> no primary streams metric
        lthr=200,
        icu_hr_zones=[120, 140, 160, 180, 999],
    )
    streams = [_stream("heartrate", data)]

    result = activity_trace(detail, streams)

    assert result["source"] == "streams"
    assert result["metric"] == "hr"
    assert result["grade_adjusted"] is False
    assert result["threshold"] == 200
    assert result["duration_s"] == 97  # sample-count fallback
    assert result["scale"] == {"lo": 50, "hi": 110}
    assert len(result["pct"]) == 96
    # bucket 0: single sample, value 100 -> on the first bound exactly.
    assert (result["pct"][0], result["zone"][0], result["value"][0]) == (50.0, 1, 100)
    # bucket 10: the dropped-out sample -> None triple.
    assert (result["pct"][10], result["zone"][10], result["value"][10]) == (
        None,
        None,
        None,
    )
    # bucket 20: value 120 lands exactly on a bound -> that zone (not above).
    assert (result["pct"][20], result["zone"][20], result["value"][20]) == (
        60.0,
        1,
        120,
    )
    # bucket 94: single sample, value 194 -> above every bound -> top zone.
    assert (result["pct"][94], result["zone"][94], result["value"][94]) == (
        97.0,
        5,
        194,
    )
    # bucket 95: merges samples 95 (195) and 96 (196) -> mean 195.5.
    assert (result["pct"][95], result["zone"][95], result["value"][95]) == (
        97.8,
        5,
        196,
    )
    assert "elevation" not in result


def test_elevation_is_cut_into_the_same_buckets_as_the_metric():
    # 3600 samples -> 96 metric buckets; the altitude array the client
    # indexes bucket-for-bucket must be 96 long too, not 3600 or its own
    # min(96, len) count.
    detail = _detail(id="i4b", type="Ride", icu_ftp=250, moving_time=3600)
    streams = [
        _stream("watts", [200.0] * 3600),
        _stream("altitude", [100.0 + i / 6 for i in range(3600)]),
    ]

    result = activity_trace(detail, streams)

    assert len(result["pct"]) == 96
    assert len(result["elevation"]) == 96
    # Bucket 0 spans samples 0..37 (mean 103.1 m), bucket 95 spans the last
    # 37 (mean 696.9 m) -- the whole climb, resampled, not a truncated head.
    assert (result["elevation"][0], result["elevation"][95]) == (103, 697)


def test_elevation_dropped_when_the_altitude_stream_is_too_short_to_align():
    # A stream shorter than the metric's bucket count cannot fill every
    # bucket, and a half-length elevation array would desync the client's
    # per-bucket readout -- drop it rather than ship a ragged pair.
    detail = _detail(id="i4c", type="Ride", icu_ftp=250)
    streams = [
        _stream("watts", [200.0] * 3600),
        _stream("altitude", [100.0 + i * 5 for i in range(50)]),  # 245 m range
    ]

    result = activity_trace(detail, streams)

    assert len(result["pct"]) == 96
    assert "elevation" not in result


# activity_trace: intervals source


def test_intervals_trace_pool_swim_recovery_none_and_dropped_rows():
    detail = _detail(
        id="i5",
        type="Swim",
        pool_length=25.0,
        threshold_pace=1.0,
        # Moving time leaves the wall rests out; the rows do not, so the
        # x-axis spans the rows' own elapsed total (3630), not this.
        moving_time=3000,
        icu_intervals=[
            # Usable via computed pct (value / threshold).
            IntervalRow(type="WORK", elapsed_time=1800, average_speed=1.05, zone=3),
            # RECOVERY: no speed, no intensity -> None triple.
            IntervalRow(type="RECOVERY", elapsed_time=30),
            # Usable via row.intensity directly; its own speed is absent, so
            # "value" (which always reflects the raw field, not pct) is None
            # even though pct/zone are not.
            IntervalRow(type="WORK", elapsed_time=1800, intensity=98.0, zone=2),
            # No elapsed_time at all -> dropped before anything else runs.
            IntervalRow(type="WORK", elapsed_time=None, average_speed=1.5),
        ],
    )

    assert activity_trace(detail, []) == {
        "activity_id": "i5",
        "source": "intervals",
        "metric": "pace",
        "grade_adjusted": False,
        "threshold": 1.0,
        "duration_s": 3630,
        "scale": {"lo": 30, "hi": 110},
        "pct": [105.0, None, 98.0],
        "zone": [3, None, 2],
        "value": [1.05, None, None],
        "frac": [0.4959, 0.0083, 0.4959],
    }


def test_intervals_trace_usable_without_a_threshold():
    detail = _detail(
        id="i6",
        type="Ride",
        icu_ftp=None,
        icu_intervals=[
            IntervalRow(type="WORK", elapsed_time=600, intensity=85.0, zone=3),
        ],
    )

    assert activity_trace(detail, []) == {
        "activity_id": "i6",
        "source": "intervals",
        "metric": "power",
        "grade_adjusted": False,
        "threshold": None,
        "duration_s": 600,
        "scale": {"lo": 30, "hi": 110},
        "pct": [85.0],
        "zone": [3],
        "value": [None],
        "frac": [1.0],
    }


def test_intervals_trace_unusable_when_no_row_has_elapsed_time():
    detail = _detail(
        id="i7",
        icu_intervals=[IntervalRow(type="WORK", elapsed_time=None, intensity=80.0)],
    )
    assert activity_trace(detail, []) == {"activity_id": "i7", "source": None}


def test_intervals_trace_unusable_when_no_row_yields_a_pct():
    detail = _detail(
        id="i8",
        icu_intervals=[IntervalRow(type="RECOVERY", elapsed_time=60)],
    )
    assert activity_trace(detail, []) == {"activity_id": "i8", "source": None}


def test_nothing_drawable_returns_null_source():
    detail = _detail(id="i9", type=None, icu_intervals=[])
    assert activity_trace(detail, []) == {"activity_id": "i9", "source": None}


# _bucket_values


def test_bucket_values_empty_data():
    assert _bucket_values([], 3) == [None, None, None]


def test_bucket_values_one_to_one_with_a_null_sample():
    assert _bucket_values([1.0, None, 3.0], 3) == [1.0, None, 3.0]


# _zone


@pytest.mark.parametrize(
    ("bounds", "x", "expected"),
    [
        ([55, 75, 90], 55, 1),  # exactly on the first bound
        ([55, 75, 90], 90, 3),  # exactly on the top bound
        ([55, 75, 90], 95, 3),  # above every bound -> top zone
        (None, 50, None),  # missing bounds
        ([], 50, None),  # empty bounds
    ],
)
def test_zone_edges(bounds, x, expected):
    assert _zone(bounds, x) == expected


# _scale


def test_scale_floor_clamp():
    assert _scale([50.0], "power") == {"lo": 30, "hi": 110}


def test_scale_cap_clamp():
    assert _scale([200.0], "power") == {"lo": 30, "hi": 160}


def test_scale_rounds_up_to_the_next_ten():
    assert _scale([123.0], "power") == {"lo": 30, "hi": 130}


def test_scale_hr_uses_a_50_floor_label():
    assert _scale([85.0], "hr") == {"lo": 50, "hi": 110}


@pytest.mark.parametrize(
    ("pct_values", "metric", "lo"),
    [
        # A hike: median 47% of LTHR sits under the 50 floor -> 40 - 20.
        ([34.0, 47.0, 64.5], "hr", 20),
        # A recovery spin: median 45% of FTP -> 40 - 20, under the 30 floor.
        ([40.0, 45.0, 48.0], "power", 20),
        # Median exactly one headroom above the floor keeps the floor.
        ([50.0, 55.0, 59.9], "power", 30),
        # Coasting the whole way: never below zero.
        ([0.0, 5.0, 12.0], "power", 0),
        # Null buckets do not drag the median.
        ([None, 80.0, None, 84.0], "hr", 50),
    ],
)
def test_scale_floor_drops_under_a_very_easy_session(pct_values, metric, lo):
    assert _scale(pct_values, metric)["lo"] == lo


def test_scale_all_null_pct_defaults_to_floor():
    assert _scale([None, None], "power") == {"lo": 30, "hi": 110}


# _round_value / _duration_s / _has_data / _row_pct / _intervals_metric


def test_round_value_pace_keeps_three_decimals():
    assert _round_value(2.45671, "pace") == 2.457


def test_round_value_power_and_hr_round_to_int():
    assert _round_value(192.4, "power") == 192
    assert _round_value(140.6, "hr") == 141


def test_duration_s_prefers_moving_time():
    detail = _detail(moving_time=100, elapsed_time=200)
    assert _duration_s(detail, 999) == 100


def test_duration_s_falls_back_to_elapsed_time():
    detail = _detail(moving_time=None, elapsed_time=200)
    assert _duration_s(detail, 999) == 200


def test_duration_s_falls_back_to_sample_count():
    detail = _detail(moving_time=None, elapsed_time=None)
    assert _duration_s(detail, 42) == 42


def test_has_data_stream_none():
    assert _has_data(None) is False


def test_has_data_all_null():
    assert _has_data(_stream("x", [None, None])) is False


def test_has_data_some_data():
    assert _has_data(_stream("x", [None, 1.0])) is True


def test_row_pct_prefers_intensity():
    row = IntervalRow(intensity=70.0, average_watts=100)
    assert _row_pct(row, 100.0, 250.0) == 70.0


def test_row_pct_computes_from_value_and_threshold():
    row = IntervalRow(intensity=None, average_watts=100)
    assert _row_pct(row, 100.0, 250.0) == 40.0


def test_row_pct_none_when_value_or_threshold_falsy():
    row = IntervalRow(intensity=None)
    assert _row_pct(row, None, 250.0) is None
    assert _row_pct(row, 100.0, None) is None


@pytest.mark.parametrize(
    ("sport", "expected"),
    [
        (SportType.CYCLING, ("power", "average_watts", 250)),
        (SportType.RUNNING, ("pace", "average_speed", 3.0)),
        (SportType.SWIMMING, ("pace", "average_speed", 3.0)),
        (None, ("hr", "average_heartrate", 160)),
    ],
)
def test_intervals_metric_by_sport(sport, expected):
    # Distinct threshold values per sport: a metric wired to the wrong
    # threshold field has to fail here, not just the wrong field name.
    detail = _detail(icu_ftp=250, threshold_pace=3.0, lthr=160)
    assert _intervals_metric(sport, detail) == expected


# _elevation


def test_elevation_included_when_range_is_wide_enough():
    assert _elevation(_stream("altitude", [100.0, 140.0]), 2) == [100, 140]


def test_elevation_excluded_when_flat():
    assert _elevation(_stream("altitude", [100.0, 110.0]), 2) is None


def test_elevation_excluded_when_a_bucket_has_no_data():
    assert _elevation(_stream("altitude", [100.0, None, 200.0]), 3) is None


def test_elevation_excluded_when_stream_missing():
    assert _elevation(None, 2) is None
