"""Tests for the wellness helpers backing the coach's recovery context."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from endury.coach.wellness_context import (
    build_wellness_summary,
    detect_trend,
    fetch_wellness,
    wellness_flags,
    wellness_trends,
)
from tests.coach.conftest import StubIntervalsClient, make_wellness

pytestmark = pytest.mark.coach

TODAY = date(2026, 1, 8)

# A fixed, hand-written noise pattern (mean 0, sd ~8): large enough that a
# naive fixed-percentage move would misread it as a trend. Authored rather
# than sampled so the test is deterministic and the series can be read at a
# glance.
NOISE_SIGMA_8 = [
    -9.0, 6.0, 11.0, -4.0, -12.0, 3.0, 8.0, -7.0, 1.0, 13.0,
    -5.0, -10.0, 4.0, 9.0, -2.0, -13.0, 7.0, 2.0, -8.0, 10.0,
    -3.0, 12.0, -6.0, -1.0, 5.0, -11.0, 0.0, 8.0,
]  # fmt: skip


#: One authored week averaging 60 with a personal sd of exactly 8.
WEEK_AT_60_SD8 = [70.0, 52.0, 66.0, 56.0, 68.0, 50.0, 58.0]
#: The same shape shifted down 4 (mean 56) — a 6.7% move, half a sd.
WEEK_AT_56 = [64.0, 48.0, 60.0, 52.0, 62.0, 50.0, 56.0]
#: The same shape shifted down ~15 (mean 44.9) — nearly two sd.
WEEK_AT_45 = [46.0, 38.0, 50.0, 42.0, 52.0, 40.0, 46.0]


def _days_back(count: int) -> list[str]:
    """Return ``count`` consecutive ISO dates ending on TODAY, oldest first."""
    return [(TODAY - timedelta(days=count - 1 - i)).isoformat() for i in range(count)]


# fetch_wellness


def test_fetch_wellness_without_client_returns_empty():
    assert fetch_wellness(None, TODAY) == []


def test_fetch_wellness_uses_a_seven_day_window_and_sorts():
    client = StubIntervalsClient(
        wellness=[make_wellness("2026-01-07"), make_wellness("2026-01-02")]
    )
    rows = fetch_wellness(client, TODAY)
    assert client.wellness_windows == [(date(2026, 1, 2), TODAY)]
    assert [row.id for row in rows] == ["2026-01-02", "2026-01-07"]


def test_fetch_wellness_drops_undated_rows():
    client = StubIntervalsClient()
    client.list_wellness = lambda oldest, newest: [  # type: ignore[method-assign]
        make_wellness("2026-01-07"),
        make_wellness("2026-01-07").model_copy(update={"id": None}),
    ]
    assert len(fetch_wellness(client, TODAY)) == 1


def test_fetch_wellness_swallows_api_errors():
    client = StubIntervalsClient(wellness_error=RuntimeError("no key"))
    assert fetch_wellness(client, TODAY) == []


def test_fetch_wellness_defaults_to_today(monkeypatch):
    client = StubIntervalsClient()
    monkeypatch.setattr("endury.common.clock.today", lambda: TODAY)
    fetch_wellness(client)
    assert client.wellness_windows == [(date(2026, 1, 2), TODAY)]


# build_wellness_summary


def test_summary_none_without_rows():
    assert build_wellness_summary([]) is None


def test_summary_reports_sleep_hrv_and_resting_hr():
    rows = [
        make_wellness("2026-01-06", sleep_secs=25200, hrv=70.0, resting_hr=48),
        make_wellness("2026-01-07", sleep_secs=28800, hrv=60.0, resting_hr=52),
    ]
    summary = build_wellness_summary(rows)
    assert summary is not None
    assert "Last 7 days" in summary
    assert "sleep 7.5h/night avg over 2 nights" in summary
    assert "HRV 60 (window avg 65)" in summary
    assert "resting HR 52 (window avg 50)" in summary


def test_summary_none_when_every_metric_is_missing():
    rows = [make_wellness("2026-01-07", sleep_secs=None, hrv=None, resting_hr=None)]
    assert build_wellness_summary(rows) is None


# wellness_flags


def test_flags_empty_without_rows():
    assert wellness_flags([]) == []


def test_flags_quiet_week_produces_nothing():
    rows = [
        make_wellness("2026-01-06", sleep_secs=28800, hrv=60.0, resting_hr=50),
        make_wellness("2026-01-07", sleep_secs=28800, hrv=59.0, resting_hr=51),
    ]
    assert wellness_flags(rows) == []


def test_short_sleep_raises_a_flag():
    rows = [make_wellness("2026-01-07", sleep_secs=18000, hrv=None, resting_hr=None)]
    flags = wellness_flags(rows)
    assert len(flags) == 1
    assert "average sleep 5.0h" in flags[0]


def test_hrv_drop_and_rhr_rise_raise_flags():
    rows = [
        make_wellness("2026-01-05", sleep_secs=None, hrv=70.0, resting_hr=48),
        make_wellness("2026-01-06", sleep_secs=None, hrv=70.0, resting_hr=48),
        make_wellness("2026-01-07", sleep_secs=None, hrv=50.0, resting_hr=60),
    ]
    flags = wellness_flags(rows)
    assert any("HRV 50 is 29% below" in flag for flag in flags)
    assert any("resting HR 60 is 12 bpm above" in flag for flag in flags)


def test_single_hrv_and_rhr_reading_cannot_flag():
    rows = [make_wellness("2026-01-07", sleep_secs=28800, hrv=20.0, resting_hr=90)]
    assert wellness_flags(rows) == []


def test_zero_hrv_baseline_is_ignored():
    rows = [
        make_wellness("2026-01-06", sleep_secs=None, hrv=0.0, resting_hr=None),
        make_wellness("2026-01-07", sleep_secs=None, hrv=0.0, resting_hr=None),
    ]
    assert wellness_flags(rows) == []


# personal-baseline gating on the flags


def _flag_rows(baseline: list[float], recent: list[float]) -> list:
    """Build dated HRV rows: 28 baseline days then 7 recent ones."""
    days = _days_back(len(baseline) + len(recent))
    return [
        make_wellness(day, sleep_secs=28800, hrv=hrv, resting_hr=None)
        for day, hrv in zip(days, baseline + recent, strict=True)
    ]


#: 28 baseline days averaging 60 with a wide personal spread (sd ~12).
NOISY_HRV_BASELINE = [72.0, 48.0] * 14
#: 28 baseline days averaging 60 with a tight personal spread (sd ~1).
STEADY_HRV_BASELINE = [61.0, 59.0] * 14


def test_hrv_drop_inside_a_noisy_athletes_own_band_does_not_flag():
    # 52 is 13% below the last six days, clearing the absolute threshold
    # alone, but for this noisy athlete it is a routine 0.65-SD move.
    rows = _flag_rows(NOISY_HRV_BASELINE, [60.0] * 6 + [52.0])
    assert wellness_flags(rows) == []


def test_the_same_hrv_drop_flags_for_a_steady_athlete():
    rows = _flag_rows(STEADY_HRV_BASELINE, [60.0] * 6 + [52.0])
    flags = wellness_flags(rows)
    assert len(flags) == 1
    assert "HRV 52 is 13% below its recent average (60)" in flags[0]


def test_a_small_hrv_dip_never_flags_even_far_outside_the_band():
    # -2.9 SD for this very steady athlete, but only 5% down: the absolute
    # threshold is still the floor, so nothing fires.
    rows = _flag_rows(STEADY_HRV_BASELINE, [60.0] * 6 + [57.0])
    assert wellness_flags(rows) == []


def test_resting_hr_rise_is_gated_by_the_personal_band_too():
    days = _days_back(35)
    baseline = [60, 40] * 14  # sd ~10.2 bpm: a wildly variable sleeper
    steady = [55, 45] * 14  # sd ~5.1 bpm
    recent = [50] * 6 + [56]  # +6 bpm clears the absolute threshold

    def rows_for(history):
        return [
            make_wellness(day, sleep_secs=28800, hrv=None, resting_hr=hr)
            for day, hr in zip(days, history + recent, strict=True)
        ]

    assert wellness_flags(rows_for(baseline)) == []
    assert "resting HR 56 is 6 bpm above" in wellness_flags(rows_for(steady))[0]


def test_only_the_recent_window_drives_the_sleep_average():
    # 35 rows in, but the sleep flag still speaks about the last 7 nights.
    days = _days_back(35)
    rows = [
        make_wellness(day, sleep_secs=28800 if i < 28 else 18000, hrv=None)
        for i, day in enumerate(days)
    ]
    flags = wellness_flags(rows)
    assert len(flags) == 1
    assert "average sleep 5.0h over the last 7 nights" in flags[0]


# detect_trend / wellness_trends


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], None),
        ([1.0, 2.0], None),
        ([60.0] * 13, None),  # 13 days is one short of a baseline
        ([60.0] * 14, "stable"),  # flat history, nothing to report
        ([60.0] * 7 + [48.0] * 7, "declining"),  # flat baseline, real drop
        ([60.0] * 7 + [72.0] * 7, "rising"),
    ],
)
def test_detect_trend(values, expected):
    assert detect_trend(values) == expected


def test_sigma_eight_noise_around_a_stable_mean_never_trends():
    # With a personal sd near 8, a flat percentage gate is too narrow and its
    # label flips as the window slides. Every trailing sub-window of one
    # noise series must read "stable".
    series = [60.0 + n for n in NOISE_SIGMA_8]
    assert [detect_trend(series[-length:]) for length in range(14, 29)] == [
        "stable"
    ] * 15


def test_a_false_trend_the_half_window_comparison_used_to_call():
    # First-half mean 60, second-half mean 56: -6.7%, clearing a flat 5%
    # threshold, but only half a standard deviation for this athlete.
    assert detect_trend([*WEEK_AT_60_SD8, *WEEK_AT_56]) == "stable"


def test_a_sustained_decline_still_reads_through_the_same_noise():
    # Same noise pattern, shifted down 12 (1.5 SD) for the last seven days.
    series = [60.0 + n for n in NOISE_SIGMA_8[:21]] + [
        48.0 + n for n in NOISE_SIGMA_8[21:]
    ]
    assert detect_trend(series) == "declining"


def test_wellness_trends_reports_each_metric_with_data():
    days = _days_back(14)
    hrv = [*WEEK_AT_60_SD8, *WEEK_AT_45]
    resting = [46, 50, 54, 50, 46, 50, 54, 56, 58, 56, 58, 56, 58, 56]
    rows = [
        make_wellness(
            day,
            sleep_secs=(6 if i < 7 else 8) * 3600,
            hrv=hrv[i],
            resting_hr=resting[i],
        )
        for i, day in enumerate(days)
    ]
    rows[-1] = rows[-1].model_copy(update={"weight": 71.5, "sleep_score": 80.0})
    trends = wellness_trends(rows)
    assert trends["sleep_hours"] == {
        "days": 14,
        "latest": 8.0,
        "baseline_avg": 6.0,
        "baseline_sd": 0.0,
        "recent_avg": 8.0,
        "trend": "rising",
    }
    assert trends["hrv"] == {
        "days": 14,
        "latest": 46.0,
        "baseline_avg": 60.0,
        "baseline_sd": 8.0,
        "recent_avg": 44.9,
        "trend": "declining",
    }
    assert trends["resting_hr"]["trend"] == "rising"
    assert trends["weight_kg"] == {
        "days": 1,
        "latest": 71.5,
        "trend": "insufficient data",
    }
    assert trends["sleep_score"]["trend"] == "insufficient data"


def test_wellness_trends_without_rows_is_empty():
    assert wellness_trends([]) == {}
